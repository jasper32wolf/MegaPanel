"""
Seed demo tenant: user, geo, taxonomy, blocks, site, build.

Usage (from repo root, venv active, Postgres up, migrations applied):

  $env:PYTHONPATH="apps/api"
  python scripts/seed_demo.py

Login: admin@demo.local / DemoPass123!
"""

from __future__ import annotations

import asyncio
import sys
import secrets
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
for pkg in ("shared", "security", "ssg"):
    sys.path.insert(0, str(ROOT / "packages" / pkg / "src"))


async def main() -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.session import open_db_session
    from app.models import Site, TaxonomyCategory, Tenant, User
    from app.models.blocks import ContentBlock
    from app.services.geo import seed_demo_geo
    from app.services.indexnow import new_indexnow_key
    from site_panel_shared.manifests import PageManifest, SiteManifest
    from site_panel_ssg import SiteBuilder

    email = "admin@demo.local"
    password = "DemoPass123!"

    async with open_db_session() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == "demo"))).scalar_one_or_none()
        if not tenant:
            tenant = Tenant(
                name="Demo Agency",
                slug="demo",
                is_demo=True,
                quotas={"pages": 5000, "llm_tokens": 500000, "leads": 5000, "domains": 50},
                branding={"company_name": "Demo Agency", "primary_color": "#1a5f4a"},
            )
            db.add(tenant)
            await db.flush()
            print(f"tenant created: {tenant.id}")
        else:
            print(f"tenant exists: {tenant.id}")

        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user:
            user = User(
                email=email,
                password_hash=hash_password(password),
                role="tenant_admin",
                tenant_id=tenant.id,
            )
            db.add(user)
            print(f"user created: {email}")
        else:
            user.tenant_id = tenant.id
            print(f"user exists: {email}")

        geo = await seed_demo_geo(db)
        print(f"geo seed: {geo}")

        cat = (
            await db.execute(
                select(TaxonomyCategory).where(
                    TaxonomyCategory.tenant_id == tenant.id,
                    TaxonomyCategory.slug == "remont-stiralok",
                )
            )
        ).scalar_one_or_none()
        if not cat:
            cat = TaxonomyCategory(
                tenant_id=tenant.id,
                niche="ремонт",
                slug="remont-stiralok",
                service="Ремонт стиральных машин",
                modifier="замена подшипника",
                method="с выездом",
                templates={
                    "title": "{service} в {city_prep}",
                    "h1": "{service} в {city_prep}",
                    "meta": "{service} в {city_prep}. Тел: {phone}",
                },
            )
            db.add(cat)
            print("taxonomy created")

        from app.services.block_library import instantiate_kit_for_site, sync_library_to_tenant

        existing_blocks = (
            await db.execute(select(ContentBlock).where(ContentBlock.tenant_id == tenant.id).limit(1))
        ).scalar_one_or_none()
        if not existing_blocks:
            await sync_library_to_tenant(db, tenant.id, "service-local-v1")
            print("blocks synced from library")

        site = (
            await db.execute(select(Site).where(Site.domain == "demo-remont.local"))
        ).scalar_one_or_none()
        if not site:
            site_id = uuid4()
            blocks, css_vars, _meta = instantiate_kit_for_site(
                "service-local-v1", site_id, service="Ремонт стиральных машин"
            )
            page = PageManifest(
                slug="/",
                title_template="Ремонт стиральных машин в {city_prep}",
                h1_template="Ремонт стиральных машин в {city_prep}",
                meta_description_template="Ремонт в {city_prep}. {phone}",
                service="Ремонт стиральных машин",
                blocks=blocks,
                unique_core="Мастера выезжают по Москве в день обращения, диагностика на месте.",
                seed=11,
            )
            manifest = SiteManifest(
                site_id=site_id,
                tenant_id=tenant.id,
                domain="demo-remont.local",
                css_vars=css_vars,
                pages=[page],
                contacts={"phone": "+7 (900) 111-22-33"},
                legal={"inn": "7700000000", "org": "ИП Демо"},
            )
            site = Site(
                id=site_id,
                tenant_id=tenant.id,
                domain="demo-remont.local",
                niche="ремонт",
                manifest=manifest.model_dump(mode="json"),
                publish_state="published",
                indexnow_key=new_indexnow_key(),
                lead_token=secrets.token_urlsafe(32),
            )
            db.add(site)
            await db.flush()
            builder = SiteBuilder(ROOT / "data" / "sites")
            result = builder.build(
                manifest,
                {
                    "city_prep": "Москве",
                    "city_nom": "Москва",
                    "city_gen": "Москвы",
                    "phone": "+7 (900) 111-22-33",
                    "service": "Ремонт стиральных машин",
                    "modifier": "Срочный",
                    "price": "от 990 ₽",
                    "lead_token": site.lead_token,
                    "lead_api_url": "/api/v1/leads/public",
                },
            )
            site.build_hash = result["build_hash"]
            print(f"site built: {site.domain} hash={site.build_hash[:12]}")
        else:
            print(f"site exists: {site.domain}")

        await db.commit()

    print("\nDone.")
    print(f"  Login: {email}")
    print(f"  Password: {password}")
    print("  Domain: demo-remont.local")


if __name__ == "__main__":
    asyncio.run(main())
