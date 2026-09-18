from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Site
from app.models.publish import BulkOperation
from app.services.audit import append_audit
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_PROTECTED_CONTACT_KEYS = {"webhook_url", "webhook_secret", "webhook_secret_enc"}


class BulkSiteSelection(BaseModel):
    site_ids: list[UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_site_ids(self) -> BulkSiteSelection:
        if len(self.site_ids) != len(set(self.site_ids)):
            raise ValueError("Site selection contains duplicates")
        return self


class BulkEditBody(BulkSiteSelection):
    contacts: dict = Field(default_factory=dict)
    css_vars: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_changes_and_protect_webhooks(self) -> BulkEditBody:
        if not self.contacts and not self.css_vars:
            raise ValueError("Select contacts or theme values to update")
        if _PROTECTED_CONTACT_KEYS & self.contacts.keys():
            raise ValueError("Configure webhooks through the site webhook endpoint")
        return self


@router.post("/edit")
async def bulk_edit(
    body: BulkEditBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    sites = list((await db.execute(select(Site).where(Site.id.in_(body.site_ids)))).scalars().all())
    if len(sites) != len(body.site_ids):
        raise HTTPException(status_code=404, detail="One or more sites were not found")
    if auth.role != "superadmin" and any(site.tenant_id != auth.tenant_id for site in sites):
        raise HTTPException(status_code=403, detail="Forbidden")

    undo: list[dict] = []
    for site in sites:
        manifest = dict(site.manifest or {})
        undo.append(
            {
                "site_id": str(site.id),
                "contacts": manifest.get("contacts"),
                "css_vars": manifest.get("css_vars"),
            }
        )
        if body.contacts:
            manifest["contacts"] = {**(manifest.get("contacts") or {}), **body.contacts}
        if body.css_vars:
            manifest["css_vars"] = {**(manifest.get("css_vars") or {}), **body.css_vars}
        site.manifest = manifest
        site.version += 1

    operation = BulkOperation(
        tenant_id=auth.tenant_id or sites[0].tenant_id,
        op_type="bulk_edit",
        status="done",
        payload=body.model_dump(mode="json"),
        result={"updated": len(sites)},
        undo_payload={"sites": undo},
        actor_id=auth.user.id,
        completed_at=datetime.now(UTC),
    )
    db.add(operation)
    await append_audit(
        db,
        action="bulk.edit",
        payload={"operation_id": str(operation.id), "count": len(sites)},
        tenant_id=operation.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(operation)
    return {"operation_id": str(operation.id), "updated": len(sites)}


@router.post("/undo/{operation_id}")
async def undo_bulk(
    operation_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    operation = (
        await db.execute(select(BulkOperation).where(BulkOperation.id == operation_id))
    ).scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")
    if auth.role != "superadmin" and operation.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if operation.op_type != "bulk_edit":
        raise HTTPException(status_code=400, detail="Undo not supported for this operation")

    for item in operation.undo_payload.get("sites", []):
        site = (
            await db.execute(select(Site).where(Site.id == UUID(item["site_id"])))
        ).scalar_one_or_none()
        if not site:
            continue
        manifest = dict(site.manifest or {})
        if "contacts" in item:
            manifest["contacts"] = item["contacts"]
        if "css_vars" in item:
            manifest["css_vars"] = item["css_vars"]
        site.manifest = manifest
        site.version += 1

    operation.status = "undone"
    await append_audit(
        db,
        action="bulk.undo",
        payload={"operation_id": str(operation_id)},
        tenant_id=operation.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"operation_id": str(operation_id), "status": "undone"}
