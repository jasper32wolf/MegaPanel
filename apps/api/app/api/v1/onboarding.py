from __future__ import annotations

import secrets
from pathlib import Path
from uuid import uuid4

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models import OnboardingSession, Site, TaxonomyCategory
from app.schemas.phase2 import HealthCheckOut, OnboardingOut, OnboardingStart, OnboardingStep
from app.services.audit import append_audit
from app.services.geo import validate_toponym
from app.services.morph import city_placeholders, inflect_cases
from fastapi import APIRouter, Depends, HTTPException
from site_panel_shared.manifests import PageManifest, SiteManifest
from site_panel_ssg import SiteBuilder
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()

STEPS = ["niche", "geo", "template", "domain", "build"]


@router.post("/start", response_model=OnboardingOut, status_code=201)
async def start_onboarding(
    body: OnboardingStart,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> OnboardingSession:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    session = OnboardingSession(
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
        step="niche",
        payload={"niche": body.niche},
    )
    db.add(session)
    await append_audit(
        db,
        action="onboarding.start",
        payload={"niche": body.niche},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/{session_id}/step", response_model=OnboardingOut)
async def advance_step(
    session_id: str,
    body: OnboardingStep,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> OnboardingSession:
    from uuid import UUID

    result = await db.execute(
        select(OnboardingSession).where(OnboardingSession.id == UUID(session_id))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if auth.role != "superadmin" and session.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = {**(session.payload or {}), **body.payload, "niche": session.payload.get("niche")}
    if body.step == "geo":
        city_name = body.payload.get("city")
        if not city_name:
            raise HTTPException(status_code=400, detail="city required")
        place = await validate_toponym(db, city_name, "city")
        if not place:
            raise HTTPException(
                status_code=400,
                detail=f"City '{city_name}' is absent from local geo. Add or import it first.",
            )
        payload["geo_id"] = str(place.id)
        payload["city"] = place.name
        payload["city_forms"] = place.name_forms

    if body.step == "template":
        service = (body.payload.get("service") or payload.get("niche") or "").strip()
        if not service:
            raise HTTPException(status_code=400, detail="Service required")
        slug = body.payload.get("slug") or "main-service"
        kit_key = body.payload.get("kit_key") or "service-local-v1"
        cat = TaxonomyCategory(
            tenant_id=session.tenant_id,
            niche=payload.get("niche", "general"),
            slug=slug,
            service=service,
            modifier=body.payload.get("modifier"),
            method=body.payload.get("method"),
            templates={
                "title": "{service} в {city_prep}",
                "h1": "{service} в {city_prep}",
                "meta": "{service} в {city_prep}. Звоните: {phone}",
            },
        )
        db.add(cat)
        await db.flush()
        payload["category_id"] = str(cat.id)
        payload["service"] = service
        payload["kit_key"] = kit_key
        from app.services.block_library import sync_library_to_tenant

        await sync_library_to_tenant(db, session.tenant_id, kit_key)

    if body.step == "domain":
        domain = (body.payload.get("domain") or "").lower().strip()
        phone = (body.payload.get("phone") or "").strip()
        if not domain or "." not in domain:
            raise HTTPException(status_code=400, detail="Valid domain required")
        if not phone:
            raise HTTPException(status_code=400, detail="Phone required")
        payload["domain"] = domain
        payload["phone"] = phone

    if body.step == "build":
        required = ("domain", "city", "service", "kit_key", "phone")
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise HTTPException(
                status_code=400, detail=f"Complete required steps: {', '.join(missing)}"
            )
        domain = payload["domain"]
        city = payload["city"]
        service = payload["service"]
        kit_key = payload["kit_key"]
        forms = payload.get("city_forms") or inflect_cases(city)
        ctx = {
            **city_placeholders(city, forms),
            "service": service,
            "modifier": payload.get("modifier") or "",
            "phone": payload["phone"],
        }
        site_id = uuid4()
        from app.services.block_library import instantiate_kit_for_site

        blocks, css_vars, theme_meta = instantiate_kit_for_site(kit_key, site_id, service=service)
        page = PageManifest(
            slug="/",
            title_template="{service} в {city_prep}",
            h1_template="{service} в {city_prep}",
            meta_description_template="{service} в {city_prep}. Тел: {phone}",
            service=service,
            blocks=blocks,
            unique_core=f"Оффер для {ctx.get('city_gen', city)}",
            seed=7,
            schema_org={"kit": theme_meta},
        )
        manifest = SiteManifest(
            site_id=site_id,
            tenant_id=session.tenant_id,
            domain=domain,
            css_vars=css_vars,
            pages=[page],
            contacts={"phone": payload["phone"]},
            context=ctx,
            legal={"org": domain, "email": f"hello@{domain}"},
        )
        site = Site(
            id=site_id,
            tenant_id=session.tenant_id,
            domain=domain,
            niche=payload.get("niche"),
            manifest=manifest.model_dump(mode="json"),
            publish_state="draft",
            lead_token=secrets.token_urlsafe(32),
        )
        db.add(site)
        await db.flush()
        ctx["lead_token"] = site.lead_token
        ctx["lead_api_url"] = "/api/v1/leads/public"
        builder = SiteBuilder(Path(settings.sites_root))
        build_result = builder.build(manifest, ctx)
        build_hash = build_result["build_hash"]
        site.build_hash = build_hash
        payload["site_id"] = str(site_id)
        payload["build_hash"] = build_hash
        payload["kit_key"] = kit_key
        session.completed = True

    session.step = body.step
    session.payload = payload
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/health-check", response_model=HealthCheckOut)
async def health_check(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> HealthCheckOut:
    details: dict = {}
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        details["db"] = str(exc)

    redis_ok = False
    try:
        from redis.asyncio import Redis

        r = Redis.from_url(settings.redis_url, decode_responses=True)
        redis_ok = bool(await r.ping())
        await r.aclose()
    except Exception as exc:  # noqa: BLE001
        details["redis"] = str(exc)

    llm_ok = bool(
        __import__("os").getenv("DEEPSEEK_API_KEY") or __import__("os").getenv("ANTHROPIC_API_KEY")
    )
    details["llm"] = "keys_present" if llm_ok else "no_api_keys"

    return HealthCheckOut(
        db_ok=db_ok,
        redis_ok=redis_ok,
        llm_ok=llm_ok,
        dns_ok=False,
        ssl_ok=False,
        details=details,
    )
