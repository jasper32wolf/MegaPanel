from __future__ import annotations

import ipaddress
import secrets
from urllib.parse import urlparse
from uuid import UUID, uuid4

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import Site
from app.models.publish import SitePage
from app.schemas.common import SiteCreate, SiteOut
from app.services.audit import append_audit
from app.services.indexnow import new_indexnow_key
from app.services.leads import get_encryptor
from app.services.morph import city_placeholders, inflect_cases
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from site_panel_shared.manifests import PageManifest, SiteManifest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class WebhookSettingsIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    secret: str = Field(min_length=16, max_length=512)


def validate_webhook_target(raw: str) -> str:
    target = raw.strip()
    if any(ord(char) < 32 for char in target):
        raise ValueError("Webhook URL is invalid")
    try:
        parsed = urlparse(target)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Webhook URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise ValueError("Webhook URL must use public HTTPS without credentials")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if "." not in parsed.hostname:
            raise ValueError("Webhook URL must use a public domain") from None
    else:
        raise ValueError("Webhook URL must use a public domain")
    return target


def render_contacts(contacts: dict) -> dict:
    return {
        key: value
        for key, value in contacts.items()
        if key not in {"webhook_url", "webhook_secret", "webhook_secret_enc"}
    }


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
    contacts = render_contacts(body.contacts or {})
    from app.services.block_library import instantiate_kit_for_site

    blocks, css_vars, _meta = instantiate_kit_for_site(
        "service-local-v1", site_id, service=body.service
    )
    page = PageManifest(
        slug="/",
        title_template="{service} в {city_prep} — {domain}",
        h1_template="{service} в {city_prep}",
        meta_description_template="Закажите {service} в {city_prep}. Телефон: {phone}",
        service=body.service,
        blocks=blocks,
        unique_core="",
        seed=42,
    )
    forms = inflect_cases(body.city)
    context = {
        **city_placeholders(body.city, forms),
        "service": body.service,
        "phone": contacts.get("phone", ""),
    }
    manifest = SiteManifest(
        site_id=site_id,
        tenant_id=tenant_id,
        domain=body.domain.lower(),
        locale=body.locale,
        css_vars=css_vars,
        pages=[page],
        contacts=contacts,
        context=context,
    )
    site = Site(
        id=site_id,
        tenant_id=tenant_id,
        domain=body.domain.lower(),
        locale=body.locale,
        manifest=manifest.model_dump(mode="json"),
        publish_state="draft",
        indexnow_key=new_indexnow_key(),
        lead_token=secrets.token_urlsafe(32),
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
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "client", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[Site]:
    stmt = select(Site).order_by(Site.created_at.desc())
    if auth.role != "superadmin":
        if not auth.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant required")
        stmt = stmt.where(Site.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{site_id}/pages")
async def list_pages(
    site_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    _ensure_tenant_access(auth, site.tenant_id)
    pages = list(
        (await db.execute(select(SitePage).where(SitePage.site_id == site_id))).scalars().all()
    )
    return [
        {
            "id": str(page.id),
            "slug": page.slug,
            "publish_state": page.publish_state,
            "index_state": page.index_state,
            "thin": page.thin,
            "content_chars": page.content_chars,
        }
        for page in pages
    ]


@router.get("/{site_id}/webhook")
async def get_webhook_settings(
    site_id: UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    _ensure_tenant_access(auth, site.tenant_id)
    contacts = (site.manifest or {}).get("contacts") or {}
    target_url = str(contacts.get("webhook_url") or "").strip() or None
    secret_configured = bool(contacts.get("webhook_secret_enc") or contacts.get("webhook_secret"))
    return {
        "target_url": target_url,
        "secret_configured": secret_configured,
        "configured": bool(target_url and secret_configured),
    }


@router.put("/{site_id}/webhook")
async def update_webhook_settings(
    site_id: UUID,
    body: WebhookSettingsIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        target_url = validate_webhook_target(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    site = (await db.execute(select(Site).where(Site.id == site_id))).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    _ensure_tenant_access(auth, site.tenant_id)

    manifest = dict(site.manifest or {})
    contacts = dict(manifest.get("contacts") or {})
    contacts["webhook_url"] = target_url
    contacts["webhook_secret_enc"] = get_encryptor().encrypt(body.secret)
    contacts.pop("webhook_secret", None)
    manifest["contacts"] = contacts
    site.manifest = manifest
    await append_audit(
        db,
        action="site.webhook.update",
        payload={"site_id": str(site.id), "target_url": target_url},
        tenant_id=site.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"target_url": target_url, "secret_configured": True, "configured": True}
