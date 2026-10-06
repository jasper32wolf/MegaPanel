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
from app.services.competitor_crawl import run_competitor_crawl
from app.services.index_schedule import prepare_due_index_schedule_batches
from app.services.intent_generation import validate_intent_page_proposal
from app.services.operations import auto_resolve_inactive_incidents
from app.services.scheduler import (
    claim_execution,
    complete_site_build_execution,
    dispatch_due_jobs,
    publish_pending_wakeups,
    recover_expired_scheduler_leases,
)
from app.services.site_build_queue import expire_stale_site_builds
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


async def bukvarix_keyword_task(ctx: dict, run_id: str) -> dict:
    """Fetch a bounded HTTPS public-free Bukvarix preview; never commit or publish."""
    try:
        async with open_db_session() as session:
            result = await run_bukvarix_keyword_run(session, uuid.UUID(run_id))
        logger.info("bukvarix_keyword_processed", run_id=run_id, status=result["status"])
        return result
    except Exception:  # noqa: BLE001
        logger.exception("bukvarix_keyword_task_failed", run_id=run_id)
        return {"status": "failed", "run_id": run_id, "error_code": "worker_execution_failed"}


async def bukvarix_keyword_sweep_task(ctx: dict) -> dict:
    """Recover and redeliver only durable Bukvarix run IDs after worker/Redis interruption."""
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


async def scheduler_execute_task(ctx: dict, scheduler_job_id: str) -> dict:
    """Execute one leased allowlisted job; it cannot publish or apply content."""
    try:
        job_id = uuid.UUID(scheduler_job_id)
    except ValueError:
        return {"status": "failed", "error_code": "invalid_scheduler_job_id"}
    async with open_db_session() as session:
        job = await claim_execution(session, job_id)
        if job is None:
            return {"status": "duplicate", "scheduler_job_id": scheduler_job_id}
        if job.work_type != "site_build":
            return await complete_site_build_execution(
                session,
                job_id=job.id,
                result={"status": "failed", "error_code": "unsupported_work_type"},
            )
        result = await run_queued_candidate_build(session, job.source_id)
        return await complete_site_build_execution(session, job_id=job.id, result=result)


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
            recovered = await recover_expired_scheduler_leases(session)
            leased = await dispatch_due_jobs(session)
            published = await publish_pending_wakeups(session)
        logger.info(
            "candidate_build_sweep",
            legacy_recovered=legacy_recovered,
            recovered=recovered,
            leased=len(leased),
            published=published,
        )
        return {
            "legacy_recovered": legacy_recovered,
            "recovered": recovered,
            "leased": len(leased),
            "published": published,
        }
    except Exception:  # noqa: BLE001
        logger.exception("candidate_build_sweep_failed")
        return {"status": "failed", "error_code": "worker_sweep_failed"}


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
    try:
        async with open_db_session() as session:
            result = await run_competitor_crawl(session, uuid.UUID(crawl_id))
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
        architecture_proposal_task,
        intent_page_proposal_task,
        bukvarix_keyword_task,
        bukvarix_keyword_sweep_task,
        scheduler_execute_task,
        candidate_build_task,
        candidate_build_sweep_task,
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
        cron(bukvarix_keyword_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(index_schedule_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(candidate_build_sweep_task, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(
            operational_incident_auto_resolve_task,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        ),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 600
    health_check_interval = 30
