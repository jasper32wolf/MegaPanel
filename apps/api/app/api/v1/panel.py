from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Lead, Site, WebhookDelivery
from app.models.project import PageDraft, PagePlan, Project
from app.models.publish import Domain, SiteBuild
from app.models.system_operation import SystemOperation
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


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
    delivery_pending = sum(
        delivery_counts.get(status, 0) for status in ("queued", "retrying", "processing")
    )
    delivery_oldest_at = await db.scalar(
        select(func.min(WebhookDelivery.created_at)).where(
            *_tenant_predicate(WebhookDelivery, auth),
            WebhookDelivery.status.in_(("queued", "retrying", "processing")),
        )
    )
    active_leads = sum(lead_counts.get(status, 0) for status in ("new", "qualified"))
    alerts = []
    if delivery_counts.get("dead_letter", 0):
        alerts.append(
            _alert(
                code="lead-delivery-dead-letter",
                severity="critical",
                title="Недоставленные заявки",
                detail="Заявки требуют ручного восстановления или настройки получателя.",
                count=delivery_counts["dead_letter"],
                route="/leads",
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
        "domains_pending_tls": domain_counts.get("pending", 0),
        "domains_tls_error": domain_counts.get("error", 0),
        "project_statuses": project_counts,
        "page_plan_statuses": plan_counts,
        "page_draft_statuses": draft_counts,
        "build_statuses": build_counts,
        "system_operation_statuses": operation_counts,
        "alerts": alerts,
    }
