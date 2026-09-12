from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Site
from app.models.publish import BulkOperation, Redirect, SitePage
from app.services.audit import append_audit
from app.services.caddy_client import CaddyClient
from app.services.indexnow import submit_indexnow

router = APIRouter()


class BulkEditBody(BaseModel):
    site_ids: list[UUID]
    contacts: dict = Field(default_factory=dict)  # phone, email
    css_vars: dict = Field(default_factory=dict)


class BulkPublishBody(BaseModel):
    site_ids: list[UUID]
    publish_state: str = Field(pattern=r"^(draft|published|archived)$")


class BulkRedirectBody(BaseModel):
    site_id: UUID
    items: list[dict]  # {from_path, to_url, code?}


class BulkIndexNowBody(BaseModel):
    site_ids: list[UUID]


@router.post("/edit")
async def bulk_edit(
    body: BulkEditBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    sites = list((await db.execute(select(Site).where(Site.id.in_(body.site_ids)))).scalars().all())
    undo: list[dict] = []
    for site in sites:
        if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
            raise HTTPException(status_code=403, detail="Forbidden")
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

    op = BulkOperation(
        tenant_id=auth.tenant_id or sites[0].tenant_id,
        op_type="bulk_edit",
        status="done",
        payload=body.model_dump(mode="json"),
        result={"updated": len(sites)},
        undo_payload={"sites": undo},
        actor_id=auth.user.id,
        completed_at=datetime.now(UTC),
    )
    db.add(op)
    await append_audit(
        db,
        action="bulk.edit",
        payload={"operation_id": str(op.id), "count": len(sites)},
        tenant_id=op.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(op)
    return {"operation_id": str(op.id), "updated": len(sites)}


@router.post("/publish")
async def bulk_publish(
    body: BulkPublishBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sites = list((await db.execute(select(Site).where(Site.id.in_(body.site_ids)))).scalars().all())
    undo = []
    for site in sites:
        if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        undo.append({"site_id": str(site.id), "publish_state": site.publish_state})
        site.publish_state = body.publish_state
        pages = list((await db.execute(select(SitePage).where(SitePage.site_id == site.id))).scalars().all())
        for p in pages:
            p.publish_state = body.publish_state
            if body.publish_state == "published" and not p.thin and p.index_state == "noindex":
                p.index_state = "queued"
    op = BulkOperation(
        tenant_id=auth.tenant_id or sites[0].tenant_id,
        op_type="bulk_publish",
        status="done",
        payload=body.model_dump(mode="json"),
        result={"updated": len(sites)},
        undo_payload={"sites": undo},
        actor_id=auth.user.id,
        completed_at=datetime.now(UTC),
    )
    db.add(op)
    await db.commit()
    await db.refresh(op)
    return {"operation_id": str(op.id), "updated": len(sites)}


@router.post("/redirects")
async def bulk_redirects(
    body: BulkRedirectBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    site = (await db.execute(select(Site).where(Site.id == body.site_id))).scalar_one_or_none()
    if not site or (auth.role != "superadmin" and site.tenant_id != auth.tenant_id):
        raise HTTPException(status_code=404, detail="Site not found")
    created = 0
    caddy = CaddyClient()
    for item in body.items:
        redir = Redirect(
            tenant_id=auth.tenant_id,
            site_id=body.site_id,
            from_path=item["from_path"],
            to_url=item["to_url"],
            code=int(item.get("code", 301)),
        )
        db.add(redir)
        await caddy.add_redirect(site.domain, redir.from_path, redir.to_url, redir.code)
        created += 1
    op = BulkOperation(
        tenant_id=auth.tenant_id,
        op_type="bulk_redirect",
        status="done",
        payload=body.model_dump(mode="json"),
        result={"created": created},
        actor_id=auth.user.id,
        completed_at=datetime.now(UTC),
    )
    db.add(op)
    await db.commit()
    await db.refresh(op)
    return {"operation_id": str(op.id), "created": created}


@router.post("/indexnow")
async def bulk_indexnow(
    body: BulkIndexNowBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    results = []
    for site_id in body.site_ids:
        site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
        if not site or not site.indexnow_key:
            continue
        if auth.role != "superadmin" and site.tenant_id != auth.tenant_id:
            continue
        pages = list(
            (
                await db.execute(
                    select(SitePage).where(SitePage.site_id == site_id, SitePage.index_state == "indexed")
                )
            ).scalars().all()
        )
        urls = [
            f"https://{site.domain}/"
            if not p.slug.strip("/")
            else f"https://{site.domain}/{p.slug.strip('/')}/"
            for p in pages
        ]
        res = await submit_indexnow(
            host=site.domain,
            key=site.indexnow_key,
            key_location=f"https://{site.domain}/{site.indexnow_key}.txt",
            urls=urls,
        )
        results.append({"site_id": str(site_id), **res})
    op = BulkOperation(
        tenant_id=auth.tenant_id,
        op_type="bulk_indexnow",
        status="done",
        payload=body.model_dump(mode="json"),
        result={"sites": results},
        actor_id=auth.user.id,
        completed_at=datetime.now(UTC),
    )
    if not op.tenant_id:
        if not body.site_ids:
            raise HTTPException(status_code=400, detail="site_ids required")
        first = (await db.execute(select(Site).where(Site.id == body.site_ids[0]))).scalar_one_or_none()
        if not first:
            raise HTTPException(status_code=404, detail="Site not found")
        op.tenant_id = first.tenant_id
    db.add(op)
    await db.commit()
    await db.refresh(op)
    return {"operation_id": str(op.id), "results": results}


@router.post("/undo/{operation_id}")
async def undo_bulk(
    operation_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    op = (await db.execute(select(BulkOperation).where(BulkOperation.id == operation_id))).scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    if auth.role != "superadmin" and op.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if op.op_type == "bulk_edit":
        for item in op.undo_payload.get("sites", []):
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
    elif op.op_type == "bulk_publish":
        for item in op.undo_payload.get("sites", []):
            site = (
                await db.execute(select(Site).where(Site.id == UUID(item["site_id"])))
            ).scalar_one_or_none()
            if site:
                site.publish_state = item["publish_state"]
    else:
        raise HTTPException(status_code=400, detail=f"Undo not supported for {op.op_type}")
    op.status = "undone"
    await append_audit(
        db,
        action="bulk.undo",
        payload={"operation_id": str(operation_id)},
        tenant_id=op.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"operation_id": str(operation_id), "status": "undone"}
