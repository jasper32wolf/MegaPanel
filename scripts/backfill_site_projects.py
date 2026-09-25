#!/usr/bin/env python3
"""Backfill draft Projects for legacy Sites without changing their releases.

Run a dry-run across all tenants:
    python scripts/backfill_site_projects.py

Review one tenant before the explicit write:
    python scripts/backfill_site_projects.py --tenant-id <UUID>
    python scripts/backfill_site_projects.py --tenant-id <UUID> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create draft Projects for legacy Sites without publishing or rebuilding them."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create reciprocal Site/Project links after reviewing the dry-run output.",
    )
    parser.add_argument(
        "--tenant-id",
        type=UUID,
        help="Limit the scan to one tenant UUID. By default all tenants are scanned.",
    )
    return parser.parse_args()


def project_slug(site_id: UUID) -> str:
    return f"site-{site_id.hex}"


def _site_label(site: object) -> str:
    return f"site_id={site.id} tenant_id={site.tenant_id} domain={site.domain}"


async def backfill(args: argparse.Namespace) -> dict[str, int]:
    from app.db.session import open_db_session
    from app.models import Project, Site
    from app.services.audit import append_audit
    from sqlalchemy import select

    mode = "apply" if args.apply else "dry-run"
    counts: Counter[str] = Counter()
    async with open_db_session() as db:
        statement = select(Site).order_by(Site.id)
        if args.tenant_id is not None:
            statement = statement.where(Site.tenant_id == args.tenant_id)
        if args.apply:
            statement = statement.with_for_update()
        sites = list((await db.execute(statement)).scalars().all())

        for site in sites:
            label = _site_label(site)
            if not isinstance(site.domain, str) or not site.domain.strip():
                counts["invalid_legacy_data"] += 1
                print(f"SKIP invalid_legacy_data {label}")
                continue
            locale = (site.locale or "ru").strip()
            if not locale or len(locale) > 10:
                counts["invalid_legacy_data"] += 1
                print(f"SKIP invalid_legacy_data {label}")
                continue

            if site.project_id is not None:
                linked = (
                    await db.execute(select(Project).where(Project.id == site.project_id))
                ).scalar_one_or_none()
                if linked is None:
                    counts["dangling_site_link"] += 1
                    print(f"SKIP dangling_site_link {label} project_id={site.project_id}")
                elif linked.site_id == site.id and linked.tenant_id == site.tenant_id:
                    counts["already_linked"] += 1
                    print(f"SKIP already_linked {label} project_id={linked.id}")
                else:
                    counts["conflicting_site_link"] += 1
                    print(f"SKIP conflicting_site_link {label} project_id={linked.id}")
                continue

            reverse_links = list(
                (await db.execute(select(Project).where(Project.site_id == site.id)))
                .scalars()
                .all()
            )
            if reverse_links:
                counts["reverse_link_conflict"] += 1
                ids = ",".join(str(project.id) for project in reverse_links)
                print(f"SKIP reverse_link_conflict {label} project_ids={ids}")
                continue

            slug = project_slug(site.id)
            slug_conflict = (
                await db.execute(
                    select(Project).where(
                        Project.tenant_id == site.tenant_id,
                        Project.slug == slug,
                    )
                )
            ).scalar_one_or_none()
            if slug_conflict is not None:
                counts["slug_conflict"] += 1
                print(f"SKIP slug_conflict {label} slug={slug} project_id={slug_conflict.id}")
                continue

            if not args.apply:
                counts["would_create"] += 1
                print(f"WOULD CREATE {label} slug={slug}")
                continue

            project = Project(
                id=uuid4(),
                tenant_id=site.tenant_id,
                site_id=site.id,
                name=site.domain,
                slug=slug,
                domain=site.domain,
                locale=locale,
                niche=site.niche,
                status="draft",
            )
            db.add(project)
            site.project_id = project.id
            await append_audit(
                db,
                action="site_project_backfill.apply",
                payload={
                    "site_id": str(site.id),
                    "project_id": str(project.id),
                    "slug": slug,
                    "domain": site.domain,
                    "mode": mode,
                },
                tenant_id=site.tenant_id,
                actor_id=None,
            )
            counts["created"] += 1
            print(f"CREATED {label} project_id={project.id} slug={slug}")

        if args.apply and counts["created"]:
            await db.commit()

    summary = dict(sorted(counts.items()))
    print(f"SUMMARY mode={mode} scanned={len(sites)} {summary}")
    return summary


def main() -> None:
    args = parse_args()
    asyncio.run(backfill(args))


if __name__ == "__main__":
    main()
