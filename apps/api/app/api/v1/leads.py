from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.core.rate_limit import client_ip, lead_limiter
from app.db.session import get_db
from app.models import Site
from app.models.leads import Consent, Lead, WebhookDelivery, WebhookDeliveryAttempt
from app.services.audit import append_audit
from app.services.leads import (
    check_honeypot,
    check_time_lock,
    cro_score,
    get_blind,
    get_encryptor,
    qualify_lead_local,
)
from app.services.webhook_delivery import create_lead_delivery, enqueue_delivery, resend_delivery
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class PublicLeadIn(BaseModel):
    site_id: UUID
    lead_token: str = Field(min_length=32, max_length=64)
    phone: str = Field(min_length=5, max_length=32)
    email: str | None = None
    name: str | None = None
    message: str | None = None
    page_slug: str | None = None
    website: str | None = None
    form_ts: float | None = None
    idempotency_key: str | None = None
    utm: dict = Field(default_factory=dict)
    consent: bool = False


class LeadStatusUpdate(BaseModel):
    status: str = Field(pattern=r"^(new|qualified|spam|sent|failed)$")
    notes: str | None = Field(default=None, max_length=4000)


class LeadExportRequest(BaseModel):
    status: str | None = Field(default=None, max_length=32)
    site_id: UUID | None = None
    q: str | None = Field(default=None, max_length=100)


def _lead_predicates(
    auth: AuthContext, *, status: str | None, site_id: UUID | None, q: str | None
) -> list:
    predicates = []
    if auth.role != "superadmin":
        predicates.append(Lead.tenant_id == auth.tenant_id)
    if status:
        predicates.append(Lead.status == status)
    if site_id:
        predicates.append(Lead.site_id == site_id)
    if q and q.strip():
        term = f"%{q.strip()}%"
        predicates.append(
            or_(
                Site.domain.ilike(term),
                Lead.page_slug.ilike(term),
                Lead.status.ilike(term),
                Lead.qualification.ilike(term),
            )
        )
    return predicates


def _csv_cell(value: object | None) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


def require_lead_owner(auth: AuthContext, lead: Lead) -> None:
    if auth.role != "superadmin" and lead.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/public", status_code=201)
async def create_public_lead(
    body: PublicLeadIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    lead_limiter.check(f"lead:{client_ip(request)}")
    if check_honeypot(body.website) or check_time_lock(body.form_ts):
        return {"ok": True, "id": "suppressed"}
    if not body.consent:
        raise HTTPException(status_code=400, detail="Consent required")

    site = (
        await db.execute(
            select(Site).where(Site.id == body.site_id, Site.lead_token == body.lead_token)
        )
    ).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    if body.idempotency_key:
        existing = await db.execute(
            select(Lead).where(
                Lead.tenant_id == site.tenant_id,
                Lead.idempotency_key == body.idempotency_key,
            )
        )
        hit = existing.scalar_one_or_none()
        if hit:
            return {"ok": True, "id": str(hit.id), "deduped": True}

    enc = get_encryptor()
    blind = get_blind()
    qualification = qualify_lead_local(body.message, body.phone)
    status = "spam" if qualification == "spam" else "new"
    if qualification == "qualified":
        status = "qualified"

    lead = Lead(
        tenant_id=site.tenant_id,
        site_id=site.id,
        page_slug=body.page_slug,
        status=status,
        phone_enc=enc.encrypt(body.phone),
        phone_blind=blind.index(body.phone),
        email_enc=enc.encrypt(body.email) if body.email else None,
        name_enc=enc.encrypt(body.name) if body.name else None,
        message=body.message,
        utm=body.utm,
        meta={"ip_hash": blind.index(client_ip(request))},
        idempotency_key=body.idempotency_key,
        qualification=qualification,
    )
    db.add(lead)
    await db.flush()
    db.add(
        Consent(
            tenant_id=site.tenant_id,
            site_id=site.id,
            visitor_id=blind.index(body.idempotency_key or str(lead.id)),
            purposes={"lead": True},
        )
    )
    delivery = None
    if status != "spam":
        delivery = await create_lead_delivery(db, lead=lead, site=site)

    await append_audit(
        db,
        action="lead.create",
        payload={
            "lead_id": str(lead.id),
            "status": lead.status,
            "delivery_id": str(delivery.id) if delivery else None,
        },
        tenant_id=site.tenant_id,
    )
    await db.commit()
    if delivery and delivery.status == "queued":
        await enqueue_delivery(delivery.id)
    return {
        "ok": True,
        "id": str(lead.id),
        "status": lead.status,
        "delivery_status": delivery.status if delivery else None,
    }


@router.get("/inbox")
async def lead_inbox(
    status: str | None = None,
    site_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    predicates = _lead_predicates(auth, status=status, site_id=site_id, q=q)
    statement = (
        select(Lead, Site.domain, WebhookDelivery.status)
        .join(Site, Site.id == Lead.site_id)
        .outerjoin(WebhookDelivery, WebhookDelivery.lead_id == Lead.id)
    )
    total_statement = select(func.count()).select_from(Lead).join(Site, Site.id == Lead.site_id)
    if predicates:
        statement = statement.where(*predicates)
        total_statement = total_statement.where(*predicates)
    rows = (
        await db.execute(
            statement.order_by(Lead.created_at.desc(), Lead.id.desc()).offset(offset).limit(limit)
        )
    ).all()
    total = (await db.execute(total_statement)).scalar_one()
    return {
        "items": [
            {
                "id": str(lead.id),
                "site_id": str(lead.site_id),
                "site_domain": site_domain,
                "page_slug": lead.page_slug,
                "status": lead.status,
                "qualification": lead.qualification,
                "crm_status": lead.crm_status,
                "delivery_status": delivery_status,
                "created_at": lead.created_at.isoformat() if lead.created_at else None,
            }
            for lead, site_domain, delivery_status in rows
        ],
        "offset": offset,
        "limit": limit,
        "total": total,
    }


@router.post("/export")
async def export_leads(
    body: LeadExportRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    predicates = _lead_predicates(auth, status=body.status, site_id=body.site_id, q=body.q)
    statement = (
        select(Lead, Site.domain, WebhookDelivery.status)
        .join(Site, Site.id == Lead.site_id)
        .outerjoin(WebhookDelivery, WebhookDelivery.lead_id == Lead.id)
        .order_by(Lead.created_at.desc(), Lead.id.desc())
    )
    if predicates:
        statement = statement.where(*predicates)
    rows = (await db.execute(statement.limit(5000))).all()
    encryptor = get_encryptor()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "site",
            "status",
            "qualification",
            "delivery",
            "page",
            "phone",
            "email",
            "name",
            "message",
            "utm",
            "created_at",
        ]
    )
    for lead, site_domain, delivery_status in rows:
        writer.writerow(
            [
                _csv_cell(lead.id),
                _csv_cell(site_domain),
                _csv_cell(lead.status),
                _csv_cell(lead.qualification),
                _csv_cell(delivery_status),
                _csv_cell(lead.page_slug),
                _csv_cell(encryptor.decrypt(lead.phone_enc) if lead.phone_enc else None),
                _csv_cell(encryptor.decrypt(lead.email_enc) if lead.email_enc else None),
                _csv_cell(encryptor.decrypt(lead.name_enc) if lead.name_enc else None),
                _csv_cell(lead.message),
                _csv_cell(json.dumps(lead.utm or {}, ensure_ascii=False, separators=(",", ":"))),
                _csv_cell(lead.created_at.isoformat() if lead.created_at else None),
            ]
        )
    await append_audit(
        db,
        action="lead.export",
        payload={
            "count": len(rows),
            "status": body.status,
            "site_id": str(body.site_id) if body.site_id else None,
            "search": bool(body.q and body.q.strip()),
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    filename = f"leads-{datetime.now(UTC).date().isoformat()}.csv"
    return Response(
        content="﻿" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{lead_id}")
async def lead_detail(
    lead_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = (
        await db.execute(
            select(Lead, Site.domain).join(Site, Site.id == Lead.site_id).where(Lead.id == lead_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    lead, site_domain = row
    require_lead_owner(auth, lead)
    return {
        "id": str(lead.id),
        "site_id": str(lead.site_id),
        "site_domain": site_domain,
        "page_slug": lead.page_slug,
        "status": lead.status,
        "qualification": lead.qualification,
        "crm_status": lead.crm_status,
        "delivery_status": (
            await db.execute(
                select(WebhookDelivery.status).where(WebhookDelivery.lead_id == lead.id)
            )
        ).scalar_one_or_none(),
        "utm": lead.utm or {},
        "notes": (lead.meta or {}).get("notes"),
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
    }


@router.post("/{lead_id}/reveal")
async def reveal_lead_pii(
    lead_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Not found")
    require_lead_owner(auth, lead)
    encryptor = get_encryptor()
    await append_audit(
        db,
        action="lead.pii_reveal",
        payload={"lead_id": str(lead.id), "fields": ["phone", "email", "name", "message"]},
        tenant_id=lead.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "id": str(lead.id),
        "phone": encryptor.decrypt(lead.phone_enc) if lead.phone_enc else None,
        "email": encryptor.decrypt(lead.email_enc) if lead.email_enc else None,
        "name": encryptor.decrypt(lead.name_enc) if lead.name_enc else None,
        "message": lead.message,
    }


@router.get("/{lead_id}/delivery")
async def lead_delivery(
    lead_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Not found")
    require_lead_owner(auth, lead)
    delivery = (
        await db.execute(select(WebhookDelivery).where(WebhookDelivery.lead_id == lead.id))
    ).scalar_one_or_none()
    if not delivery:
        return {"delivery": None, "attempts": []}
    attempts = list(
        (
            await db.execute(
                select(WebhookDeliveryAttempt)
                .where(WebhookDeliveryAttempt.delivery_id == delivery.id)
                .order_by(WebhookDeliveryAttempt.sequence)
            )
        )
        .scalars()
        .all()
    )
    return {
        "delivery": {
            "id": str(delivery.id),
            "target": delivery.target_url,
            "status": delivery.status,
            "attempt_count": delivery.attempt_count,
            "max_attempts": delivery.max_attempts,
            "next_attempt_at": delivery.next_attempt_at.isoformat()
            if delivery.next_attempt_at
            else None,
            "last_error": delivery.last_error,
            "last_http_status": delivery.last_http_status,
        },
        "attempts": [
            {
                "sequence": attempt.sequence,
                "trigger": attempt.trigger,
                "status": attempt.status,
                "http_status": attempt.http_status,
                "error": attempt.error,
                "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
                "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
            }
            for attempt in attempts
        ],
    }


@router.post("/{lead_id}/delivery/resend", status_code=202)
async def resend_lead_delivery(
    lead_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Not found")
    require_lead_owner(auth, lead)
    delivery = (
        await db.execute(
            select(WebhookDelivery).where(WebhookDelivery.lead_id == lead.id).with_for_update()
        )
    ).scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=409, detail="No webhook delivery is configured")
    try:
        await resend_delivery(db, delivery)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await append_audit(
        db,
        action="lead.webhook.resend",
        payload={"lead_id": str(lead.id), "delivery_id": str(delivery.id)},
        tenant_id=lead.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await enqueue_delivery(delivery.id, trigger="manual")
    return {"id": str(delivery.id), "status": delivery.status}


@router.patch("/{lead_id}")
async def update_lead(
    lead_id: UUID,
    body: LeadStatusUpdate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Not found")
    require_lead_owner(auth, lead)
    old_status = lead.status
    lead.status = body.status
    notes_updated = "notes" in body.model_fields_set
    if notes_updated:
        meta = dict(lead.meta or {})
        notes = body.notes.strip() if body.notes else ""
        if notes:
            meta["notes"] = notes
        else:
            meta.pop("notes", None)
        lead.meta = meta
    await append_audit(
        db,
        action="lead.update",
        payload={
            "lead_id": str(lead.id),
            "from": old_status,
            "to": body.status,
            "notes_updated": notes_updated,
        },
        tenant_id=lead.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"id": str(lead.id), "status": lead.status, "notes": (lead.meta or {}).get("notes")}


@router.get("/cro/{page_type}")
async def cro_checklist(
    page_type: str,
    present: str = "",
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
) -> dict:
    items = [x for x in present.split(",") if x]
    return cro_score(page_type, items)
