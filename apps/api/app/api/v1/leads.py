from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.core.rate_limit import client_ip, lead_limiter
from app.db.session import get_db
from app.models import Site
from app.models.leads import Lead
from app.services.audit import append_audit
from app.services.leads import (
    check_honeypot,
    check_time_lock,
    cro_score,
    dispatch_webhook,
    get_blind,
    get_encryptor,
    qualify_lead_local,
)

router = APIRouter()


class PublicLeadIn(BaseModel):
    site_id: UUID
    phone: str = Field(min_length=5, max_length=32)
    email: str | None = None
    name: str | None = None
    message: str | None = None
    page_slug: str | None = None
    website: str | None = None  # honeypot
    form_ts: float | None = None
    idempotency_key: str | None = None
    utm: dict = Field(default_factory=dict)
    consent: bool = False


class LeadStatusUpdate(BaseModel):
    status: str = Field(pattern=r"^(new|qualified|spam|sent|failed)$")
    notes: str | None = None


@router.post("/public", status_code=201)
async def create_public_lead(
    body: PublicLeadIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    lead_limiter.check(f"lead:{client_ip(request)}")
    if check_honeypot(body.website) or check_time_lock(body.form_ts):
        # Soft-success to bots
        return {"ok": True, "id": "suppressed"}
    if not body.consent:
        raise HTTPException(status_code=400, detail="Consent required")

    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
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

    from app.services.plugins import emit_hooks

    hooks = await emit_hooks(
        db,
        "lead.created",
        {"lead_id": str(lead.id), "tenant_id": str(site.tenant_id), "status": lead.status},
    )
    if hooks:
        lead.meta = {**(lead.meta or {}), "hooks": hooks}

    # Webhook from site contacts if configured
    webhook_url = (site.manifest.get("contacts") or {}).get("webhook_url")
    webhook_secret = (site.manifest.get("contacts") or {}).get("webhook_secret", "dev-secret")
    if webhook_url and status != "spam":
        payload = {
            "lead_id": str(lead.id),
            "site_id": str(site.id),
            "domain": site.domain,
            "page_slug": body.page_slug,
            "qualification": qualification,
            "idempotency_key": body.idempotency_key or str(lead.id),
        }
        result = await dispatch_webhook(webhook_url, payload, webhook_secret)
        lead.crm_status = "sent" if result.get("ok") else "failed"
        lead.status = "sent" if result.get("ok") else "failed"
        lead.meta = {**(lead.meta or {}), "webhook": result}

    await append_audit(
        db,
        action="lead.create",
        payload={"lead_id": str(lead.id), "status": lead.status},
        tenant_id=site.tenant_id,
    )
    await db.commit()
    return {"ok": True, "id": str(lead.id), "status": lead.status}


@router.get("/inbox")
async def lead_inbox(
    status: str | None = None,
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(100)
    if auth.role != "superadmin":
        stmt = stmt.where(Lead.tenant_id == auth.tenant_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    rows = list((await db.execute(stmt)).scalars().all())
    enc = get_encryptor()
    out = []
    for r in rows:
        phone = enc.decrypt(r.phone_enc) if r.phone_enc else None
        out.append(
            {
                "id": str(r.id),
                "site_id": str(r.site_id),
                "page_slug": r.page_slug,
                "status": r.status,
                "qualification": r.qualification,
                "phone": phone,
                "utm": r.utm,
                "crm_status": r.crm_status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


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
    if auth.role != "superadmin" and lead.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    lead.status = body.status
    if body.notes:
        lead.meta = {**(lead.meta or {}), "notes": body.notes}
    await db.commit()
    return {"id": str(lead.id), "status": lead.status}


@router.get("/cro/{page_type}")
async def cro_checklist(
    page_type: str,
    present: str = "",
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
) -> dict:
    items = [x for x in present.split(",") if x]
    return cro_score(page_type, items)
