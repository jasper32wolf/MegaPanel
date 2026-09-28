from __future__ import annotations

import asyncio
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
    latest_success = (
        await db.execute(
            select(SiteBuild)
            .where(*_tenant_predicate(SiteBuild, auth), SiteBuild.status == "success")
            .order_by(SiteBuild.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
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
    return {
        "observed_at": now.isoformat(),
        "scope": "tenant_database_and_local_uploads",
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
            "references": "not_observed",
            "references_reason": "No persisted page/block media reference relation",
        },
        "system": {
            "operation_status_counts": operation_statuses,
            "backups": {
                "status": "not_observed",
                "reason": "No persisted backup completion or restore-drill result",
            },
        },
        "worker": {
            "status": "not_observed",
            "reason": "No persisted worker heartbeat or job-completion freshness signal",
        },
    }
