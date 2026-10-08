from __future__ import annotations

import uuid

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app.api.v1.ai_providers import _adapter
from app.api.v1.ai_workspace import _actual_cost, _usage_payload, _validate_proposal
from app.api.v1.projects import run_queued_candidate_build
from app.core.config import get_settings
from app.db.session import open_db_session
from app.models import AIProviderConnection, AIRun, SchedulerJob
from app.providers import ProviderError, StructuredRequest
from app.services.ai_secrets import decrypt_provider_key
from app.services.audit import append_audit
from app.services.bukvarix_queue import (
    due_bukvarix_keyword_run_ids,
    enqueue_bukvarix_keyword_run,
    recover_stale_bukvarix_keyword_runs,
    run_bukvarix_keyword_run,
)
from app.services.competitor_crawl import recover_legacy_competitor_crawls, run_competitor_crawl
from app.services.index_schedule import prepare_due_index_schedule_batches
from app.services.intent_generation import validate_intent_page_proposal
from app.services.operations import auto_resolve_inactive_incidents
from app.services.operator_alerts import process_due_alert_deliveries
from app.services.scheduler import (
    claim_execution,
    complete_execution,
    dispatch_due_jobs,
    publish_pending_wakeups,
    recover_expired_scheduler_leases,
)
from app.services.site_build_queue import expire_stale_site_builds
from app.services.site_integrity import verify_published_release_integrity
from app.services.site_monitor import monitor_published_domains
from app.services.telemetry_retention import rollup_and_purge
from app.services.webhook_delivery import due_delivery_ids, process_delivery, recover_expired_leases
from app.services.worker_heartbeat import record_worker_heartbeat

# Provider requests are intentionally made only by this worker, never by the API route.

logger = structlog.get_logger("worker")
settings = get_settings()


async def healthcheck_task(ctx: dict) -> dict:
    logger.info("worker_healthcheck")
    return {"ok": True}


async def worker_heartbeat_task(ctx: dict) -> dict:
    async with open_db_session() as session:
        await record_worker_heartbeat(session)
    logger.info("worker_heartbeat_recorded")
    return {"ok": True}


async def webhook_delivery_task(ctx: dict, delivery_id: str, trigger: str = "automatic") -> dict:
    try:
        async with open_db_session() as session:
            result = await process_delivery(session, uuid.UUID(delivery_id), trigger=trigger)
        logger.info("webhook_delivery_processed", delivery_id=delivery_id, status=result["status"])
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("webhook_delivery_failed", delivery_id=delivery_id, error=str(exc))
        return {"error": str(exc)}


async def webhook_delivery_sweep_task(ctx: dict) -> dict:
    try:
        async with open_db_session() as session:
            recovered = await recover_expired_leases(session)
            ids = await due_delivery_ids(session)
        results = []
        for delivery_id in ids:
            async with open_db_session() as session:
                results.append(await process_delivery(session, delivery_id))
        logger.info("webhook_delivery_sweep", recovered=recovered, processed=len(results))
        return {"recovered": recovered, "processed": len(results)}
    except Exception as exc:  # noqa: BLE001
        logger.error("webhook_delivery_sweep_failed", error=str(exc))
        return {"error": str(exc)}


async def operator_alert_delivery_sweep_task(ctx: dict) -> dict:
    try:
        async with open_db_session() as session:
            processed = await process_due_alert_deliveries(session)
        logger.info("operator_alert_delivery_sweep", processed=processed)
        return {"processed": processed}
    except Exception:  # noqa: BLE001
        logger.exception("operator_alert_delivery_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


async def site_monitor_sweep_task(ctx: dict) -> dict:
    try:
        async with open_db_session() as session:
            monitored = await monitor_published_domains(session)
        logger.info("site_monitor_sweep", monitored=monitored)
        return {"monitored": monitored}
    except Exception:  # noqa: BLE001
        logger.exception("site_monitor_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


async def site_integrity_sweep_task(ctx: dict) -> dict:
    try:
        async with open_db_session() as session:
            checked = await verify_published_release_integrity(session)
        logger.info("site_integrity_sweep", checked=checked)
        return {"checked": checked}
    except Exception:  # noqa: BLE001
        logger.exception("site_integrity_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


async def bukvarix_keyword_task(ctx: dict, run_id: str) -> dict:
    """Compatibility handler for legacy Bukvarix messages outside scheduler ownership."""
    try:
        async with open_db_session() as session:
            source_id = uuid.UUID(run_id)
            scheduler_job = None
            if hasattr(session, "scalar"):
                scheduler_job = await session.scalar(
                    select(SchedulerJob.id).where(
                        SchedulerJob.work_type == "bukvarix_keyword",
                        SchedulerJob.source_id == source_id,
                    )
                )
            if scheduler_job:
                return {"status": "scheduler_managed", "run_id": run_id}
            result = await run_bukvarix_keyword_run(session, source_id)
        logger.info("bukvarix_keyword_processed", run_id=run_id, status=result["status"])
        return result
    except Exception:  # noqa: BLE001
        logger.exception("bukvarix_keyword_task_failed", run_id=run_id)
        return {"status": "failed", "run_id": run_id, "error_code": "worker_execution_failed"}


async def bukvarix_keyword_sweep_task(ctx: dict) -> dict:
    """Redeliver only legacy runs; scheduler-owned work uses the generic outbox."""
    try:
        async with open_db_session() as session:
            recovered = await recover_stale_bukvarix_keyword_runs(session)
            run_ids = await due_bukvarix_keyword_run_ids(session)
        enqueued = 0
        for run_id in run_ids:
            try:
                await enqueue_bukvarix_keyword_run(run_id)
                enqueued += 1
            except Exception:  # noqa: BLE001
                logger.warning("bukvarix_keyword_enqueue_deferred", run_id=str(run_id))
        logger.info(
            "bukvarix_keyword_sweep",
            recovered=recovered,
            queued=len(run_ids),
            enqueued=enqueued,
        )
        return {"recovered": recovered, "queued": len(run_ids), "enqueued": enqueued}
    except Exception:  # noqa: BLE001
        logger.exception("bukvarix_keyword_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


async def scheduler_execute_task(
    ctx: dict, scheduler_job_id: str, lease_id: str | None = None
) -> dict:
    """Execute one fenced lease; older job-only wakeups cannot claim newer attempts."""
    try:
        job_id = uuid.UUID(scheduler_job_id)
        lease = uuid.UUID(lease_id) if lease_id else None
    except (TypeError, ValueError):
        return {"status": "failed", "error_code": "invalid_scheduler_job_id"}
    if lease is None:
        return {"status": "stale_lease", "scheduler_job_id": scheduler_job_id}
    async with open_db_session() as session:
        job = await claim_execution(session, job_id, lease)
        if job is None:
            return {"status": "duplicate", "scheduler_job_id": scheduler_job_id}
        if job.work_type == "site_build":
            result = await run_queued_candidate_build(
                session, job.source_id, scheduler_lease_id=lease
            )
        elif job.work_type == "bukvarix_keyword":
            result = await run_bukvarix_keyword_run(session, job.source_id)
        elif job.work_type == "competitor_crawl":
            result = await run_competitor_crawl(session, job.source_id)
        else:
            result = {"status": "failed", "error_code": "unsupported_work_type"}
        return await complete_execution(session, job_id=job.id, lease_id=lease, result=result)


async def candidate_build_task(ctx: dict, build_id: str) -> dict:
    """Compatibility handler for legacy messages that cannot bypass scheduler leases."""
    try:
        async with open_db_session() as session:
            source_id = uuid.UUID(build_id)
            scheduler_job = None
            if hasattr(session, "scalar"):
                scheduler_job = await session.scalar(
                    select(SchedulerJob.id).where(
                        SchedulerJob.work_type == "site_build",
                        SchedulerJob.source_id == source_id,
                    )
                )
            if scheduler_job:
                return {"status": "scheduler_managed", "build_id": build_id}
            result = await run_queued_candidate_build(session, source_id)
        logger.info("candidate_build_processed", build_id=build_id, status=result["status"])
        return result
    except Exception:  # noqa: BLE001
        # The durable record is recovered by its lease/sweep path; do not leak execution details.
        logger.exception("candidate_build_task_failed", build_id=build_id)
        return {"status": "failed", "build_id": build_id, "error_code": "worker_execution_failed"}


async def candidate_build_sweep_task(ctx: dict) -> dict:
    """Recover, fairly lease and wake frozen candidates; never publish a release."""
    try:
        async with open_db_session() as session:
            legacy_recovered = await expire_stale_site_builds(session)
            legacy_crawls_recovered = await recover_legacy_competitor_crawls(session)
            recovered = await recover_expired_scheduler_leases(session)
            leased = await dispatch_due_jobs(session)
            published = await publish_pending_wakeups(session)
        logger.info(
            "candidate_build_sweep",
            legacy_recovered=legacy_recovered,
            legacy_crawls_recovered=legacy_crawls_recovered,
            recovered=recovered,
            leased=len(leased),
            published=published,
        )
        return {
            "legacy_recovered": legacy_recovered,
            "legacy_crawls_recovered": legacy_crawls_recovered,
            "recovered": recovered,
            "leased": len(leased),
            "published": published,
        }
    except Exception:  # noqa: BLE001
        logger.exception("candidate_build_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


async def telemetry_retention_task(ctx: dict) -> dict:
    """Commit daily counts and raw-event deletion together; no external delivery."""
    try:
        async with open_db_session() as session:
            result = await rollup_and_purge(session)
        logger.info("telemetry_retention_completed", **result)
        return result
    except Exception:  # noqa: BLE001
        logger.exception("telemetry_retention_failed")
        return {"status": "failed", "error_code": "telemetry_retention_failed"}


async def index_schedule_sweep_task(ctx: dict) -> dict:
    """Prepare at most one due index batch as a private candidate; never publish it."""
    try:
        async with open_db_session() as session:
            prepared = await prepare_due_index_schedule_batches(session)
        logger.info("index_schedule_sweep", prepared=len(prepared))
        return {"prepared": prepared}
    except Exception:  # noqa: BLE001
        logger.exception("index_schedule_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


async def competitor_crawl_task(ctx: dict, crawl_id: str) -> dict:
    """Execute only legacy crawls not owned by the fair scheduler."""
    try:
        async with open_db_session() as session:
            source_id = uuid.UUID(crawl_id)
            scheduler_job = None
            if hasattr(session, "scalar"):
                scheduler_job = await session.scalar(
                    select(SchedulerJob.id).where(
                        SchedulerJob.work_type == "competitor_crawl",
                        SchedulerJob.source_id == source_id,
                    )
                )
            if scheduler_job:
                return {"status": "scheduler_managed", "crawl_id": crawl_id}
            result = await run_competitor_crawl(session, source_id)
        logger.info("competitor_crawl_processed", crawl_id=crawl_id, status=result["status"])
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("competitor_crawl_failed", crawl_id=crawl_id, error=str(exc))
        return {"status": "failed", "crawl_id": crawl_id}


async def operational_incident_auto_resolve_task(ctx: dict) -> dict:
    async with open_db_session() as session:
        resolved = await auto_resolve_inactive_incidents(session)
    logger.info("operational_incidents_auto_resolved", resolved=resolved)
    return {"resolved": resolved}


async def architecture_proposal_task(ctx: dict, run_id: str) -> dict:
    """Execute one reserved architecture run; safe to invoke repeatedly."""
    async with open_db_session() as session:
        run = (
            await session.execute(
                select(AIRun).where(AIRun.id == uuid.UUID(run_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            return {"status": "missing", "run_id": run_id}
        if run.status != "reserved":
            return {"status": run.status, "run_id": run_id, "duplicate": True}
        envelope = dict(run.execution_envelope or {})
        run.status = "running"
        run.error_code = None
        await session.commit()

        if envelope.get("tenant_id") != str(run.tenant_id):
            run.status = "failed"
            run.error_code = "tenant_scope_violation"
            await session.commit()
            return {"status": run.status, "run_id": run_id, "error_code": run.error_code}
        estimated_cost = float(run.cost_usd or 0.0)
        pricing = envelope.get("pricing") or {}
        max_cost = float(envelope.get("max_cost_usd") or 0.0)
        try:
            connection = await session.get(
                AIProviderConnection, uuid.UUID(envelope["provider_connection_id"])
            )
            if not connection or not connection.enabled:
                raise RuntimeError("provider_connection_unavailable")
            adapter = _adapter(connection, decrypt_provider_key(connection.encrypted_api_key))
            response = await adapter.generate_structured(
                StructuredRequest(
                    model=envelope["model"],
                    system_prompt=envelope["system_prompt"],
                    user_prompt=envelope["user_prompt"],
                    output_schema=envelope["output_schema"],
                    temperature=float(envelope.get("temperature", 0.2)),
                    max_tokens=int(envelope["max_output_tokens"]),
                )
            )
            validation = envelope["validation"]
            pages = _validate_proposal(
                response.data,
                keyword_ids=set(validation["keyword_ids"]),
                geo_ids=set(validation["geo_ids"]),
                fact_keys=set(validation["fact_keys"]),
                catalogs={key: set(value) for key, value in validation["catalogs"].items()},
            )
            usage = _usage_payload(response.usage)
            actual_cost = max(
                estimated_cost,
                _actual_cost(response.usage, pricing, estimated_cost),
            )
            cost_exceeded = actual_cost > max_cost
            run.provider_id = response.provider_id
            run.model_id = response.model
            run.request_id = response.request_id
            run.output = {"pages": pages}
            run.usage = usage
            run.cost_usd = actual_cost
            run.error_code = (
                "actual_cost_exceeded_limit"
                if cost_exceeded
                else "usage_unavailable"
                if not response.usage.known
                else None
            )
            run.status = "failed" if cost_exceeded else "pending_approval"
            audit_action = (
                "ai.architecture.proposal.cost_limit_exceeded"
                if cost_exceeded
                else "ai.architecture.proposal.created"
            )
        except ProviderError as exc:
            run.status = "failed"
            run.error_code = exc.code
            run.usage = _usage_payload(exc.usage)
            run.cost_usd = max(
                estimated_cost,
                _actual_cost(exc.usage, pricing, estimated_cost),
            )
            run.request_id = exc.request_id
            audit_action = "ai.run.failed"
        except (ValueError, TypeError):
            run.status = "failed"
            run.error_code = "invalid_ai_output"
            response_usage = locals().get("response")
            usage_obj = response_usage.usage if response_usage else None
            run.usage = _usage_payload(usage_obj)
            run.cost_usd = max(estimated_cost, _actual_cost(usage_obj, pricing, estimated_cost))
            run.request_id = response_usage.request_id if response_usage else None
            audit_action = "ai.run.failed"
        except Exception:  # noqa: BLE001
            run.status = "failed"
            run.error_code = "provider_execution_failed"
            # Unknown provider spend is charged at least at the reservation.
            run.cost_usd = estimated_cost
            audit_action = "ai.run.failed"
        await append_audit(
            session,
            action=audit_action,
            payload={"run_id": run_id, "status": run.status, "error_code": run.error_code},
            tenant_id=run.tenant_id,
            actor_id=None,
        )
        await session.commit()
        return {"status": run.status, "run_id": run_id, "error_code": run.error_code}


async def intent_page_proposal_task(ctx: dict, run_id: str) -> dict:
    """Produce one frozen intent/design proposal; never materialize or publish a page."""
    async with open_db_session() as session:
        run = (
            await session.execute(
                select(AIRun).where(AIRun.id == uuid.UUID(run_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            return {"status": "missing", "run_id": run_id}
        if run.status != "reserved":
            return {"status": run.status, "run_id": run_id, "duplicate": True}
        envelope = dict(run.execution_envelope or {})
        run.status = "running"
        run.error_code = None
        await session.commit()
        estimated_cost = float(run.cost_usd or 0.0)
        pricing = envelope.get("pricing") or {}
        max_cost = float(envelope.get("max_cost_usd") or 0.0)
        try:
            if envelope.get("tenant_id") != str(run.tenant_id):
                raise ValueError("tenant_scope_violation")
            connection = await session.get(
                AIProviderConnection, uuid.UUID(envelope["provider_connection_id"])
            )
            if not connection or not connection.enabled:
                raise RuntimeError("provider_connection_unavailable")
            response = await _adapter(
                connection, decrypt_provider_key(connection.encrypted_api_key)
            ).generate_structured(
                StructuredRequest(
                    model=envelope["model"],
                    system_prompt=envelope["system_prompt"],
                    user_prompt=envelope["user_prompt"],
                    output_schema=envelope["output_schema"],
                    temperature=float(envelope.get("temperature", 0.2)),
                    max_tokens=int(envelope["max_output_tokens"]),
                )
            )
            validation = envelope["validation"]
            proposal = validate_intent_page_proposal(
                response.data,
                source_binding=validation["source_binding"],
                allowed_block_slots=validation["allowed_block_slots"],
                fact_keys=set(validation["fact_keys"]),
                semantic_project_keyword_ids=set(validation["semantic_project_keyword_ids"]),
            )
            actual_cost = max(estimated_cost, _actual_cost(response.usage, pricing, estimated_cost))
            run.provider_id = response.provider_id
            run.model_id = response.model
            run.request_id = response.request_id
            run.output = {"proposal": proposal}
            run.usage = _usage_payload(response.usage)
            run.cost_usd = actual_cost
            run.error_code = (
                "actual_cost_exceeded_limit"
                if actual_cost > max_cost
                else "usage_unavailable"
                if not response.usage.known
                else None
            )
            run.status = "failed" if actual_cost > max_cost else "pending_approval"
            audit_action = (
                "ai.intent_page.cost_limit_exceeded"
                if actual_cost > max_cost
                else "ai.intent_page.proposal.created"
            )
        except ProviderError as exc:
            run.status = "failed"
            run.error_code = exc.code
            run.usage = _usage_payload(exc.usage)
            run.cost_usd = max(estimated_cost, _actual_cost(exc.usage, pricing, estimated_cost))
            run.request_id = exc.request_id
            audit_action = "ai.run.failed"
        except (KeyError, TypeError, ValueError):
            run.status = "failed"
            run.error_code = "invalid_ai_output"
            response_obj = locals().get("response")
            usage_obj = response_obj.usage if response_obj else None
            run.usage = _usage_payload(usage_obj)
            run.cost_usd = max(estimated_cost, _actual_cost(usage_obj, pricing, estimated_cost))
            run.request_id = response_obj.request_id if response_obj else None
            audit_action = "ai.run.failed"
        except Exception:  # noqa: BLE001
            run.status = "failed"
            run.error_code = "provider_execution_failed"
            run.cost_usd = estimated_cost
            audit_action = "ai.run.failed"
        await append_audit(
            session,
            action=audit_action,
            payload={"run_id": run_id, "status": run.status, "error_code": run.error_code},
            tenant_id=run.tenant_id,
            actor_id=None,
        )
        await session.commit()
        return {"status": run.status, "run_id": run_id, "error_code": run.error_code}


class WorkerSettings:
    functions = [
        healthcheck_task,
        worker_heartbeat_task,
        webhook_delivery_task,
        webhook_delivery_sweep_task,
        operator_alert_delivery_sweep_task,
        site_monitor_sweep_task,
        site_integrity_sweep_task,
        architecture_proposal_task,
        intent_page_proposal_task,
        bukvarix_keyword_task,
        bukvarix_keyword_sweep_task,
        scheduler_execute_task,
        candidate_build_task,
        candidate_build_sweep_task,
        telemetry_retention_task,
        index_schedule_sweep_task,
        competitor_crawl_task,
        operational_incident_auto_resolve_task,
    ]
    cron_jobs = [
        cron(
            worker_heartbeat_task,
            second=set(range(0, 60, settings.worker_heartbeat_interval_seconds)),
            run_at_startup=True,
        ),
        cron(webhook_delivery_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(
            operator_alert_delivery_sweep_task,
            minute=set(range(0, 60, settings.operator_alert_delivery_interval_seconds // 60)),
        ),
        cron(
            site_monitor_sweep_task,
            minute=set(range(0, 60, settings.site_monitor_interval_minutes)),
        ),
        cron(
            site_integrity_sweep_task,
            minute=set(range(0, 60, settings.site_monitor_interval_minutes)),
        ),
        cron(bukvarix_keyword_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(index_schedule_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(candidate_build_sweep_task, minute=set(range(60)), run_at_startup=True),
        cron(telemetry_retention_task, hour=3, minute=17, run_at_startup=True),
        cron(
            operational_incident_auto_resolve_task,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        ),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    health_check_interval = 30
