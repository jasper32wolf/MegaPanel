from __future__ import annotations

import shutil
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Site
from app.models.publish import SiteBuild, SitePage
from app.schemas.common import SiteCreate, SiteOut
from app.services.audit import append_audit
from app.services.caddy_client import CaddyClient
from app.services.indexnow import new_indexnow_key
from site_panel_shared.manifests import PageManifest, SiteManifest
from site_panel_ssg import SiteBuilder

router = APIRouter()
settings = get_settings()


def _ensure_tenant_access(auth: AuthContext, tenant_id: UUID) -> None:
    if auth.role == "superadmin":
        return
    if auth.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


@router.post("", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
async def create_site(
    body: SiteCreate,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> Site:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    tenant_id = auth.tenant_id

    existing = await db.execute(select(Site).where(Site.domain == body.domain.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Domain already registered")

    site_id = uuid4()
    from app.services.block_library import instantiate_kit_for_site

    blocks, css_vars, _meta = instantiate_kit_for_site("service-local-v1", site_id, service="Услуги")
    page = PageManifest(
        slug="/",
        title_template="{service} в {city_prep} — {domain}",
        h1_template="{service} в {city_prep}",
        meta_description_template="Закажите {service} в {city_prep}. Телефон: {phone}",
        service="Услуги",
        blocks=blocks,
        unique_core="Локальный оффер — scaffold",
        seed=42,
    )
    manifest = SiteManifest(
        site_id=site_id,
        tenant_id=tenant_id,
        domain=body.domain.lower(),
        locale=body.locale,
        css_vars=css_vars,
        pages=[page],
        contacts=body.contacts or {"phone": "+7 (900) 000-00-00"},
    )
    site = Site(
        id=site_id,
        tenant_id=tenant_id,
        domain=body.domain.lower(),
        locale=body.locale,
        manifest=manifest.model_dump(mode="json"),
        publish_state="draft",
        indexnow_key=new_indexnow_key(),
    )
    db.add(site)
    await append_audit(
        db,
        action="site.create",
        payload={"domain": site.domain},
        tenant_id=tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(site)
    return site


@router.get("", response_model=list[SiteOut])
async def list_sites(
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "client", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[Site]:
    stmt = select(Site).order_by(Site.created_at.desc())
    if auth.role != "superadmin":
        if not auth.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant required")
        stmt = stmt.where(Site.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/{site_id}/build")
async def build_site(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    _ensure_tenant_access(auth, site.tenant_id)

    if not site.indexnow_key:
        site.indexnow_key = new_indexnow_key()

    manifest = SiteManifest.model_validate(site.manifest)
    # Preserve previous build for rollback
    root = Path(settings.sites_root) / str(site.id)
    current = root / "current"
    previous = root / "previous"
    if current.exists():
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        current.rename(previous)

    pages_rows = (
        await db.execute(select(SitePage).where(SitePage.site_id == site.id))
    ).scalars().all()
    index_states = {p.slug: p.index_state for p in pages_rows}

    builder = SiteBuilder(Path(settings.sites_root))
    context = {
        "service": "Услуги",
        "city_prep": "Москве",
        "city_nom": "Москва",
        "phone": (site.manifest.get("contacts") or {}).get("phone", ""),
        **(site.manifest.get("contacts") or {}),
    }
    t0 = time.perf_counter()
    build_result = builder.build(manifest, context, index_states=index_states)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    build_hash = build_result["build_hash"]

    # Write IndexNow key file
    key_file = builder.site_dir(str(site.id)) / f"{site.indexnow_key}.txt"
    key_file.write_text(site.indexnow_key, encoding="utf-8")

    # Sync site_pages from build meta
    existing_by_slug = {p.slug: p for p in pages_rows}
    for meta in build_result["pages"]:
        slug = meta["slug"]
        row = existing_by_slug.get(slug)
        if not row:
            row = SitePage(
                site_id=site.id,
                tenant_id=site.tenant_id,
                slug=slug,
                publish_state=site.publish_state,
                index_state=meta["index_state"],
            )
            db.add(row)
        row.content_chars = meta["content_chars"]
        row.thin = meta["thin"]
        if meta["thin"]:
            row.index_state = "noindex"
        elif slug not in index_states:
            row.index_state = meta["index_state"]

    site.previous_build_hash = site.build_hash
    site.build_hash = build_hash
    site.version += 1

    build_rec = SiteBuild(
        site_id=site.id,
        tenant_id=site.tenant_id,
        status="success",
        build_hash=build_hash,
        previous_build_hash=site.previous_build_hash,
        pages_built=len(build_result["pages"]),
        duration_ms=duration_ms,
        log=f"indexed={build_result['indexed_count']}",
    )
    db.add(build_rec)

    # Best-effort Caddy configure
    caddy = CaddyClient()
    caddy_result = await caddy.upsert_site_vhost(
        site.domain,
        str(builder.site_dir(str(site.id))),
        noindex_paths=[m["path"] for m in build_result["pages"] if m["index_state"] != "indexed"],
    )
    site.caddy_configured = bool(caddy_result.get("ok"))

    await append_audit(
        db,
        action="site.build",
        payload={"build_hash": build_hash, "domain": site.domain, "caddy": caddy_result},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "site_id": str(site.id),
        "build_hash": build_hash,
        "previous_build_hash": site.previous_build_hash,
        "version": site.version,
        "pages_built": len(build_result["pages"]),
        "indexed_count": build_result["indexed_count"],
        "duration_ms": duration_ms,
        "caddy": caddy_result,
    }


@router.post("/{site_id}/rollback")
async def rollback_site(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    _ensure_tenant_access(auth, site.tenant_id)
    if not site.previous_build_hash:
        raise HTTPException(status_code=400, detail="No previous build")
    builder = SiteBuilder(Path(settings.sites_root))
    ok = builder.rollback(str(site.id), site.previous_build_hash)
    if not ok:
        # Soft swap even if hash mismatch file missing
        root = Path(settings.sites_root) / str(site.id)
        prev, current = root / "previous", root / "current"
        if not prev.exists():
            raise HTTPException(status_code=400, detail="Previous build directory missing")
        backup = root / "rollback_tmp"
        if current.exists():
            current.rename(backup)
        prev.rename(current)
        if backup.exists():
            backup.rename(prev)
    old = site.build_hash
    site.build_hash, site.previous_build_hash = site.previous_build_hash, old
    await append_audit(
        db,
        action="site.rollback",
        payload={"build_hash": site.build_hash},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"build_hash": site.build_hash, "previous_build_hash": site.previous_build_hash}
