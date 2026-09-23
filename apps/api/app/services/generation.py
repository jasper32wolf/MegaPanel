from __future__ import annotations

from typing import Any
from uuid import UUID

from app.models.project import PagePlan, Project, ProjectFactRevision
from app.services.block_library import instantiate_kit_for_site
from site_panel_shared.manifests import PageManifest

GENERATOR_VERSION = "deterministic-v1"


def _service_from_facts(facts: dict[str, Any]) -> str:
    service = facts.get("service")
    if isinstance(service, str) and service.strip():
        return service.strip()
    services = facts.get("services")
    if isinstance(services, list):
        for value in services:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _primary_geo(plan: PagePlan) -> dict[str, Any]:
    for item in (plan.geo_snapshot or {}).get("items", []):
        if item.get("role") == "primary":
            return item
    return {}


def create_page_draft(
    *,
    project: Project,
    plan: PagePlan,
    facts: ProjectFactRevision,
) -> tuple[dict, dict, str]:
    fact_values = facts.facts or {}
    service = _service_from_facts(fact_values)
    primary_geo = _primary_geo(plan)
    city = str(primary_geo.get("name") or "")
    blocks, _css_vars, kit_meta = instantiate_kit_for_site(
        plan.kit_key,
        str(project.id),
        service=service or "Услуги",
    )
    selected_block_ids = (getattr(plan, "block_selection", None) or {}).get("blocks")
    if selected_block_ids is not None:
        blocks_by_type = {block.type: block for block in blocks}
        if len(selected_block_ids) != len(set(selected_block_ids)) or any(
            block_id not in blocks_by_type for block_id in selected_block_ids
        ):
            raise ValueError("PagePlan contains an invalid curated block selection")
        blocks = [
            blocks_by_type[block_id].model_copy(update={"order": order})
            for order, block_id in enumerate(selected_block_ids)
        ]
    city_prep = (primary_geo.get("forms") or {}).get("prep") or city
    title = f"{service} в {city_prep}".strip() if city else service
    manifest = PageManifest(
        slug=plan.slug,
        title_template="{service} в {city_prep} — {domain}",
        h1_template="{service} в {city_prep}",
        meta_description_template="{service} в {city_prep}. Контакты: {phone}",
        service=service or "Услуги",
        geo_id=UUID(primary_geo["geo_id"]) if primary_geo.get("geo_id") else None,
        blocks=blocks,
        unique_core=str(fact_values.get("unique_core") or ""),
        seed=plan.version,
    )
    input_snapshot = {
        "project_id": str(project.id),
        "project_domain": project.domain,
        "fact_revision_id": str(facts.id),
        "facts_hash": facts.facts_hash,
        "facts": fact_values,
        "keyword_snapshot": plan.keyword_snapshot or {},
        "geo_snapshot": plan.geo_snapshot or {},
        "kit_key": plan.kit_key,
        "plan_version": plan.version,
    }
    generator_meta = {
        "generator_version": GENERATOR_VERSION,
        "kit": kit_meta,
        "title_hint": title,
        "tokens": 0,
        "cost_usd": 0,
    }
    content = "\n".join(
        [
            manifest.title_template,
            manifest.h1_template,
            manifest.meta_description_template,
            manifest.unique_core or "",
        ]
    )
    return (
        manifest.model_dump(mode="json"),
        {**input_snapshot, "generator_meta": generator_meta},
        content,
    )
