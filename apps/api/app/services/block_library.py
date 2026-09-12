"""Block Factory library sync + kit instantiation (TZ §6)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blocks import BlockKit, BlockKitItem, ContentBlock, make_hash_class
from site_panel_blocks import instantiate_blocks, library_version, list_kits, load_kit
from site_panel_security import sanitize_html
from site_panel_shared.manifests import BlockDef


async def upsert_global_kits(session: AsyncSession) -> list[str]:
    """Mirror on-disk kits into block_kits / block_kit_items tables."""
    keys: list[str] = []
    for meta in list_kits():
        kit = load_kit(meta["key"])
        row = (await session.execute(select(BlockKit).where(BlockKit.key == kit.key))).scalar_one_or_none()
        if not row:
            row = BlockKit(key=kit.key, name=kit.name, version=kit.version)
            session.add(row)
            await session.flush()
        else:
            # refresh items
            existing = list(
                (await session.execute(select(BlockKitItem).where(BlockKitItem.kit_id == row.id))).scalars().all()
            )
            for item in existing:
                await session.delete(item)
            await session.flush()
        row.name = kit.name
        row.version = kit.version
        row.description = kit.description
        row.niches = kit.niches
        row.theme = kit.theme.model_dump()
        row.meta = {"library_version": library_version()}
        for i, block in enumerate(kit.blocks):
            clean = sanitize_html(block.html) or block.html
            session.add(
                BlockKitItem(
                    kit_id=row.id,
                    block_type=block.type,
                    sort=i,
                    name=block.name,
                    html=clean,
                    css=block.css,
                    props={**block.props, "cro": block.cro},
                )
            )
        keys.append(kit.key)
    await session.flush()
    return keys


async def sync_library_to_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kit_key: str = "service-local-v1",
) -> dict[str, Any]:
    kit = load_kit(kit_key)
    await upsert_global_kits(session)
    # deactivate previous library blocks for this kit
    rows = list(
        (
            await session.execute(
                select(ContentBlock).where(
                    ContentBlock.tenant_id == tenant_id,
                    ContentBlock.kit_key == kit_key,
                    ContentBlock.source == "library",
                )
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.is_active = False

    created = 0
    for block in kit.blocks:
        clean = sanitize_html(block.html)
        if not clean.strip():
            continue
        session.add(
            ContentBlock(
                tenant_id=tenant_id,
                type=block.type,
                name=block.name,
                hash_class=make_hash_class(block.type, clean),
                html=clean,
                css=block.css,
                props={**block.props, "cro": block.cro},
                kit_key=kit.key,
                library_version=library_version(),
                source="library",
                is_active=True,
            )
        )
        created += 1
    await session.flush()
    return {"synced": created, "kit_key": kit.key, "library_version": library_version()}


def instantiate_kit_for_site(
    kit_key: str,
    site_id: uuid.UUID | str,
    *,
    service: str = "Услуги",
) -> tuple[list[BlockDef], dict[str, str], dict[str, Any]]:
    kit = load_kit(kit_key)
    instances, css_vars = instantiate_blocks(kit.blocks, site_id, kit.theme)
    # Map CSS vars to both --sp-* keys (stored without prefix) and legacy primary-color
    mapped = {
        "primary-color": css_vars.get("sp-primary", "#0f6e5c"),
        "bg": css_vars.get("sp-bg", "#f7f9f8"),
        "text": css_vars.get("sp-text", "#14201c"),
        **css_vars,
    }
    blocks = [
        BlockDef(
            type=b["type"],
            hash_class=b["hash_class"],
            html=b["html"],
            css=b["css"],
            props=b.get("props") or {},
            order=b["order"],
        )
        for b in instances
    ]
    theme_meta = {"kit_key": kit.key, "library_version": library_version(), "service": service}
    return blocks, mapped, theme_meta


def preview_kit_html(kit_key: str, seed: str = "preview") -> dict[str, Any]:
    kit = load_kit(kit_key)
    instances, css_vars = instantiate_blocks(kit.blocks, seed, kit.theme)
    ctx = {
        "service": "Ремонт",
        "modifier": "Срочный",
        "city_nom": "Москва",
        "city_gen": "Москвы",
        "city_prep": "Москве",
        "city_dat": "Москве",
        "phone": "+7 (900) 000-00-00",
        "price": "990 ₽",
        "unique_core": "Локальный оффер для превью комплекта.",
    }
    from site_panel_ssg.templates import fill_slots

    parts = []
    css_parts = []
    for b in instances:
        html = fill_slots(b["html"], ctx)
        parts.append(f'<section class="{b["hash_class"]}" data-block="{b["type"]}">{html}</section>')
        if b["css"]:
            css_parts.append(b["css"])
    body = "\n".join(parts)
    style = "\n".join(css_parts)
    return {
        "kit_key": kit.key,
        "html": f"<style>{style}</style>\n{body}",
        "css_vars": css_vars,
        "theme": kit.theme.model_dump(),
        "blocks": [b["type"] for b in instances],
    }
