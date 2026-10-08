from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.system import require_system_operator
from app.db.session import get_db
from app.models import (
    AlertDelivery,
    AlertIncident,
    Lead,
    Notification,
    OperationalEvent,
    Site,
    WebhookDelivery,
    WorkerHeartbeat,
)
from app.models.leads import LeadDeliveryAggregate, LeadRoutingPolicy
from app.models.project import PageDraft, PagePlan, Project
from app.models.publish import Domain, SiteBuild
from app.models.system_operation import SystemOperation
from app.services.audit import append_audit
from app.services.metrics import (
    record_active_incidents,
    record_delivery_queue_age,
    record_worker_heartbeat,
)
from app.services.operations import (
    _serialize_incident,
    list_verification_projection,
    serialize_operational_event,
    transition_incident,
)
from app.services.operator_alerts import alert_channel_status, create_operator_alert
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

ALERT_CATEGORIES = frozenset({"security", "site", "system"})


def _serialize_notification(
    notification: Notification,
    deliveries: list[AlertDelivery],
    subject_label: str | None = None,
) -> dict:
    return {
        "id": str(notification.id),
        "category": notification.category,
        "signal_code": notification.signal_code,
        "subject_kind": notification.subject_kind,
        "subject_key": notification.subject_key,
        "subject_label": subject_label,
        "priority": notification.priority,
        "title": notification.title,
        "body": notification.body,
        "read": notification.read,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
        "deliveries": {delivery.channel: delivery.status for delivery in deliveries},
    }


async def _notification_projection(
    db: AsyncSession, notifications: list[Notification]
) -> list[dict]:
    if not notifications:
        return []
    rows = list(
        (
            await db.execute(
                select(AlertDelivery).where(
                    AlertDelivery.notification_id.in_(
                        [notification.id for notification in notifications]
                    )
                )
            )
        ).scalars()
    )
    by_notification: dict[UUID, list[AlertDelivery]] = {}
    for delivery in rows:
        by_notification.setdefault(delivery.notification_id, []).append(delivery)
    subject_ids: dict[str, set[UUID]] = {"domain": set(), "site": set()}
    for notification in notifications:
        if notification.subject_kind not in subject_ids or not notification.subject_key:
            continue
        try:
            subject_ids[notification.subject_kind].add(UUID(notification.subject_key))
        except ValueError:
            continue
    labels: dict[tuple[str, str], str] = {}
    if subject_ids["domain"]:
        domains = list(
            await db.execute(select(Domain).where(Domain.id.in_(subject_ids["domain"]))).scalars()
        )
        labels.update({("domain", str(domain.id)): domain.hostname for domain in domains})
    if subject_ids["site"]:
        sites = list(
            await db.execute(select(Site).where(Site.id.in_(subject_ids["site"]))).scalars()
        )
        labels.update({("site", str(site.id)): site.domain for site in sites})
    return [
        _serialize_notification(
            notification,
            by_notification.get(notification.id, []),
            labels.get((notification.subject_kind or "", notification.subject_key or "")),
        )
        for notification in notifications
    ]


router = APIRouter()


class IncidentActionIn(BaseModel):
    action: Literal["acknowledge", "resolve", "snooze"]
    snooze_minutes: Literal[60, 240, 1440] | None = None

    @model_validator(mode="after")
    def validate_snooze(self) -> IncidentActionIn:
        if self.action == "snooze" and self.snooze_minutes is None:
            raise ValueError("snooze_minutes is required for snooze")
        if self.action != "snooze" and self.snooze_minutes is not None:
            raise ValueError("snooze_minutes is only allowed for snooze")
        return self


@router.get("/builds/{site_id}")
async def build_history(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from app.models.publish import SiteBuild

    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = list(
        (
            await db.execute(
                select(SiteBuild)
                .where(SiteBuild.site_id == site_id)
                .order_by(SiteBuild.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(b.id),
            "status": b.status,
            "build_hash": b.build_hash,
            "previous_build_hash": b.previous_build_hash,
            "pages_built": b.pages_built,
            "duration_ms": b.duration_ms,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in rows
    ]


def _tenant_predicate(model: type, auth: AuthContext) -> list:
    return [model.tenant_id == auth.tenant_id] if auth.tenant_id else []


async def _status_counts(
    db: AsyncSession,
    model: type,
    auth: AuthContext,
    field_name: str = "status",
) -> dict[str, int]:
    field = getattr(model, field_name)
    rows = await db.execute(
        select(field, func.count()).where(*_tenant_predicate(model, auth)).group_by(field)
    )
    return dict(rows.all())


def _alert(
    *,
    code: str,
    severity: str,
    title: str,
    detail: str,
    count: int,
    route: str,
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "count": count,
        "route": route,
    }


def _manifest_media_reference_observation(pages: list) -> dict:
    asset_ids: set[UUID] = set()
    referenced_pages = 0
    invalid_entries = 0
    for page in pages:
        manifest = page.manifest or {}
        if not isinstance(manifest, dict):
            invalid_entries += 1
            continue
        page_has_reference = False
        media = manifest.get("media") or []
        block_media = manifest.get("block_media") or {}
        if not isinstance(media, list) or not isinstance(block_media, dict):
            invalid_entries += 1
            continue
        attachments = [*media, *block_media.values()]
        for attachment in attachments:
            try:
                asset_id = UUID(str((attachment or {}).get("asset_id")))
            except (AttributeError, TypeError, ValueError):
                invalid_entries += 1
                continue
            asset_ids.add(asset_id)
            page_has_reference = True
        referenced_pages += int(page_has_reference)
    return {
        "status": "manifest_snapshot",
        "source": "materialized_site_page_manifest",
        "assets": len(asset_ids),
        "pages": referenced_pages,
        "invalid_entries": invalid_entries,
    }


async def _latest_ready_candidate(db: AsyncSession, auth: AuthContext) -> SiteBuild | None:
    return (
        await db.execute(
            select(SiteBuild)
            .where(*_tenant_predicate(SiteBuild, auth), SiteBuild.status == "ready")
            .order_by(SiteBuild.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _worker_heartbeat_observation(
    last_seen_at: datetime | None, now: datetime, stale_after_seconds: int
) -> dict:
    if last_seen_at is None:
        return {
            "status": "not_observed",
            "last_heartbeat_at": None,
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
            "reason": "No persisted worker heartbeat has been recorded",
        }
    age_seconds = max(0, int((now - last_seen_at).total_seconds()))
    if age_seconds > stale_after_seconds:
        return {
            "status": "stale",
            "last_heartbeat_at": last_seen_at.isoformat(),
            "age_seconds": age_seconds,
            "stale_after_seconds": stale_after_seconds,
            "reason": "The persisted worker heartbeat is older than its freshness threshold",
        }
    return {
        "status": "healthy",
        "last_heartbeat_at": last_seen_at.isoformat(),
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
        "reason": None,
    }


@router.get("/reports/summary")
async def report_summary(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "client")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")

    sites = list(
        (await db.execute(select(Site).where(*_tenant_predicate(Site, auth)))).scalars().all()
    )
    (
        lead_counts,
        delivery_counts,
        domain_counts,
        project_counts,
        plan_counts,
        draft_counts,
        build_counts,
        operation_counts,
    ) = (
        await _status_counts(db, Lead, auth),
        await _status_counts(db, WebhookDelivery, auth),
        await db.execute(
            select(Domain.ssl_status, func.count())
            .where(*_tenant_predicate(Domain, auth))
            .group_by(Domain.ssl_status)
        ),
        await _status_counts(db, Project, auth),
        await _status_counts(db, PagePlan, auth, "state"),
        await _status_counts(db, PageDraft, auth, "state"),
        await _status_counts(db, SiteBuild, auth),
        await _status_counts(db, SystemOperation, auth),
    )
    domain_counts = dict(domain_counts.all())
    aggregate_counts = await _status_counts(db, LeadDeliveryAggregate, auth)
    routing_counts = await _status_counts(db, LeadRoutingPolicy, auth, "state")
    active_routing_site_ids = set(
        (
            await db.execute(
                select(LeadRoutingPolicy.site_id).where(
                    *_tenant_predicate(LeadRoutingPolicy, auth),
                    LeadRoutingPolicy.state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    routing_missing = sum(
        site.publish_state == "published"
        and getattr(site, "project_id", None) is not None
        and site.id not in active_routing_site_ids
        for site in sites
    )
    delivery_pending = sum(
        delivery_counts.get(status, 0) for status in ("queued", "retrying", "processing")
    )
    delivery_oldest_at = await db.scalar(
        select(func.min(WebhookDelivery.created_at)).where(
            *_tenant_predicate(WebhookDelivery, auth),
            WebhookDelivery.status.in_(("queued", "retrying", "processing")),
        )
    )
    record_delivery_queue_age(
        age_seconds=(
            max(0, int((datetime.now(UTC) - delivery_oldest_at).total_seconds()))
            if delivery_oldest_at
            else None
        )
    )
    active_leads = sum(lead_counts.get(status, 0) for status in ("new", "qualified"))
    alerts = []
    delivery_attention = aggregate_counts.get("attention", 0) or delivery_counts.get(
        "dead_letter", 0
    )
    if delivery_attention:
        alerts.append(
            _alert(
                code="lead-delivery-dead-letter",
                severity="critical",
                title="Недоставленные заявки",
                detail="Требуемый получатель заявки требует ручного восстановления или настройки.",
                count=delivery_attention,
                route="/leads",
            )
        )
    if routing_missing:
        alerts.append(
            _alert(
                code="lead-routing-missing",
                severity="warning",
                title="Нет активной политики маршрутизации",
                detail="Для опубликованного сайта настройте и явно активируйте получателей заявок.",
                count=routing_missing,
                route="/projects",
            )
        )
    if routing_counts.get("review", 0):
        alerts.append(
            _alert(
                code="lead-routing-review",
                severity="info",
                title="Маршрутизация ожидает проверки",
                detail="Проверьте и явно активируйте policy до следующей публикации.",
                count=routing_counts["review"],
                route="/projects",
            )
        )
    if domain_counts.get("error", 0):
        alerts.append(
            _alert(
                code="domain-tls-error",
                severity="critical",
                title="Ошибки DNS или TLS",
                detail="Проверьте конкретный домен до публикации или после изменения DNS.",
                count=domain_counts["error"],
                route="/domains",
            )
        )
    if draft_counts.get("failed", 0):
        alerts.append(
            _alert(
                code="page-draft-failed",
                severity="warning",
                title="Неуспешные черновики страниц",
                detail="Откройте проект и проверьте причину сбоя перед повторной генерацией.",
                count=draft_counts["failed"],
                route="/projects",
            )
        )
    if plan_counts.get("review", 0) or draft_counts.get("review", 0):
        alerts.append(
            _alert(
                code="review-queue",
                severity="info",
                title="Ожидается ручная проверка",
                detail="Одобрение плана или черновика не публикует сайт автоматически.",
                count=plan_counts.get("review", 0) + draft_counts.get("review", 0),
                route="/projects",
            )
        )
    if operation_counts.get("failed", 0):
        alerts.append(
            _alert(
                code="system-operation-failed",
                severity="warning",
                title="Неуспешная системная операция",
                detail="Проверьте историю операции и связанный GitHub workflow.",
                count=operation_counts["failed"],
                route="/system",
            )
        )

    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "sites": len(sites),
        "published_sites": sum(site.publish_state == "published" for site in sites),
        "pages_estimate": sum(len((site.manifest or {}).get("pages") or []) for site in sites),
        "leads": sum(lead_counts.values()),
        "active_leads": active_leads,
        "lead_statuses": lead_counts,
        "delivery_pending": delivery_pending,
        "delivery_dead_letter": delivery_counts.get("dead_letter", 0),
        "delivery_oldest_at": delivery_oldest_at.isoformat() if delivery_oldest_at else None,
        "delivery_statuses": delivery_counts,
        "lead_delivery_aggregate_statuses": aggregate_counts,
        "lead_routing_policy_statuses": routing_counts,
        "lead_routing_missing": routing_missing,
        "domains_pending_tls": domain_counts.get("pending", 0),
        "domains_tls_error": domain_counts.get("error", 0),
        "project_statuses": project_counts,
        "page_plan_statuses": plan_counts,
        "page_draft_statuses": draft_counts,
        "build_statuses": build_counts,
        "system_operation_statuses": operation_counts,
        "alerts": alerts,
    }


@router.get("/incidents")
async def list_incidents(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    incidents = list(
        (
            await db.execute(
                select(AlertIncident)
                .where(*_tenant_predicate(AlertIncident, auth))
                .order_by(AlertIncident.opened_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    record_active_incidents(
        count=sum(incident.status in {"open", "acknowledged"} for incident in incidents)
    )
    return [_serialize_incident(incident) for incident in incidents]


@router.patch("/incidents/{incident_id}")
async def update_incident(
    incident_id: UUID,
    body: IncidentActionIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    incident = (
        await db.execute(
            select(AlertIncident).where(AlertIncident.id == incident_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if auth.role != "superadmin" and incident.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        await transition_incident(
            db,
            incident=incident,
            action=body.action,
            snooze_minutes=body.snooze_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await append_audit(
        db,
        action=f"operational_incident.{body.action}",
        payload={
            "incident_id": str(incident.id),
            "status": incident.status,
            **({"snooze_minutes": body.snooze_minutes} if body.snooze_minutes else {}),
            **(
                {"snoozed_until": incident.snoozed_until.isoformat()}
                if incident.snoozed_until
                else {}
            ),
        },
        tenant_id=incident.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_incident(incident)


@router.get("/alerts")
async def list_operator_alerts(
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if category is not None and category not in ALERT_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unsupported alert category")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    filters = [*_tenant_predicate(Notification, auth)]
    if category:
        filters.append(Notification.category == category)
    total = await db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    unread = (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*filters, Notification.read.is_(False))
        )
        or 0
    )
    notifications = list(
        (
            await db.execute(
                select(Notification)
                .where(*filters)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    return {
        "items": await _notification_projection(db, notifications),
        "total": total,
        "unread": unread,
        "channels": alert_channel_status(),
    }


@router.post("/alerts/{notification_id}/read")
async def mark_operator_alert_read(
    notification_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    notification = (
        await db.execute(
            select(Notification).where(Notification.id == notification_id).with_for_update()
        )
    ).scalar_one_or_none()
    if notification is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if auth.role != "superadmin" and notification.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    notification.read = True
    await db.commit()
    return (await _notification_projection(db, [notification]))[0]


@router.post("/alerts/test")
async def send_test_operator_alert(
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    notification = create_operator_alert(
        db,
        tenant_id=auth.tenant_id,
        category="system",
        signal_code="operator-alert-test",
        title="Проверка оповещений",
        body="Это проверочное сообщение панели. Оно не меняет состояние сайтов или инфраструктуры.",
        subject_kind="system",
        user_id=auth.user.id,
    )
    await append_audit(
        db,
        action="operator_alert.test",
        payload={"notification_id": str(notification.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return (await _notification_projection(db, [notification]))[0]


@router.get("/events")
async def list_operational_events(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    events = list(
        (
            await db.execute(
                select(OperationalEvent)
                .where(*_tenant_predicate(OperationalEvent, auth))
                .order_by(OperationalEvent.occurred_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [serialize_operational_event(event) for event in events]


@router.get("/reports/verification")
async def report_verification(
    auth: AuthContext = Depends(require_system_operator),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return {
        "scope": "bounded recorded verification evidence; not a production-readiness certification",
        "checks": await list_verification_projection(db),
    }


@router.get("/reports/observability")
async def report_observability(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "client")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")

    from pathlib import Path

    from app.core.config import get_settings
    from app.models import AIRun, DeadLetterJob, GenerationJob, MediaAsset, SitePage

    build_statuses = await _status_counts(db, SiteBuild, auth)
    ai_statuses = await _status_counts(db, AIRun, auth)
    generation_statuses = await _status_counts(db, GenerationJob, auth)
    operation_statuses = await _status_counts(db, SystemOperation, auth)
    delivery_statuses = await _status_counts(db, WebhookDelivery, auth)
    media_assets = list(
        (await db.execute(select(MediaAsset).where(*_tenant_predicate(MediaAsset, auth))))
        .scalars()
        .all()
    )
    pages = list(
        (await db.execute(select(SitePage).where(*_tenant_predicate(SitePage, auth))))
        .scalars()
        .all()
    )
    latest_build = (
        await db.execute(
            select(SiteBuild)
            .where(*_tenant_predicate(SiteBuild, auth))
            .order_by(SiteBuild.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_success = await _latest_ready_candidate(db, auth)
    failed_ai = list(
        (
            await db.execute(
                select(AIRun.error_code, func.count())
                .where(*_tenant_predicate(AIRun, auth), AIRun.status == "failed")
                .group_by(AIRun.error_code)
            )
        ).all()
    )
    ai_reserved = await db.scalar(
        select(func.coalesce(func.sum(AIRun.cost_usd), 0)).where(
            *_tenant_predicate(AIRun, auth), AIRun.status == "reserved"
        )
    )
    ai_recorded = await db.scalar(
        select(func.coalesce(func.sum(AIRun.cost_usd), 0)).where(
            *_tenant_predicate(AIRun, auth), AIRun.status != "reserved"
        )
    )
    unresolved_dlq = await db.scalar(
        select(func.count())
        .select_from(DeadLetterJob)
        .where(*_tenant_predicate(DeadLetterJob, auth), DeadLetterJob.resolved.is_(False))
    )
    now = datetime.now(UTC)
    latest_worker_heartbeat = await db.scalar(
        select(WorkerHeartbeat.last_seen_at).where(WorkerHeartbeat.worker_key == "arq").limit(1)
    )
    worker = _worker_heartbeat_observation(
        latest_worker_heartbeat,
        now,
        get_settings().worker_heartbeat_stale_after_seconds,
    )
    record_worker_heartbeat(age_seconds=worker["age_seconds"], status=worker["status"])
    missing_media = 0
    provenance_gaps = 0
    expired_media = 0
    root = await asyncio.to_thread(lambda: Path(get_settings().uploads_root).resolve())
    for asset in media_assets:
        try:
            path = await asyncio.to_thread(lambda asset_path=asset.path: Path(asset_path).resolve())
            path.relative_to(root)
            if not await asyncio.to_thread(path.is_file):
                missing_media += 1
        except ValueError:
            missing_media += 1
        provenance = (asset.meta or {}).get("provenance") or {}
        hashes = (asset.meta or {}).get("hashes") or {}
        if (
            provenance.get("kind") != "manual_upload"
            or provenance.get("rights_confirmed") is not True
            or not hashes.get("stored_sha256")
        ):
            provenance_gaps += 1
        expires = provenance.get("license_expires_at")
        if isinstance(expires, str) and expires < now.date().isoformat():
            expired_media += 1
    media_references = _manifest_media_reference_observation(pages)
    return {
        "observed_at": now.isoformat(),
        "scope": "tenant_database_local_uploads_and_global_worker_heartbeat",
        "builds": {
            "status_counts": build_statuses,
            "failed": build_statuses.get("failed", 0),
            "latest": {
                "status": latest_build.status,
                "build_hash": latest_build.build_hash,
                "created_at": latest_build.created_at.isoformat()
                if latest_build.created_at
                else None,
            }
            if latest_build
            else None,
            "latest_success": {
                "build_hash": latest_success.build_hash,
                "created_at": latest_success.created_at.isoformat()
                if latest_success.created_at
                else None,
            }
            if latest_success
            else None,
        },
        "ai": {
            "status_counts": ai_statuses,
            "failed_error_codes": {str(code or "unknown"): count for code, count in failed_ai},
            "reserved_estimated_usd": float(ai_reserved or 0),
            "recorded_cost_usd": float(ai_recorded or 0),
            "generation_status_counts": generation_statuses,
            "unresolved_dead_letter_jobs": int(unresolved_dlq or 0),
        },
        "delivery": {
            "status_counts": delivery_statuses,
            "pending": sum(
                delivery_statuses.get(item, 0) for item in ("queued", "retrying", "processing")
            ),
            "dead_letter": delivery_statuses.get("dead_letter", 0),
        },
        "domains": {
            "status_source": "last_persisted_domain_row",
            "tls_status_counts": dict(
                (
                    await db.execute(
                        select(Domain.ssl_status, func.count())
                        .where(*_tenant_predicate(Domain, auth))
                        .group_by(Domain.ssl_status)
                    )
                ).all()
            ),
        },
        "content_gaps": {
            "thin_pages": sum(page.thin for page in pages),
            "noindex_pages": sum(page.index_state == "noindex" for page in pages),
        },
        "media": {
            "assets": len(media_assets),
            "missing_files": missing_media,
            "provenance_gaps": provenance_gaps,
            "expired_licenses": expired_media,
            "references": media_references,
        },
        "system": {
            "operation_status_counts": operation_statuses,
            "backups": {
                "status": "not_observed",
                "reason": "No persisted backup completion or restore-drill result",
            },
        },
        "worker": worker,
    }
