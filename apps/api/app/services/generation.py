from __future__ import annotations

from typing import Any
from uuid import UUID

from app.models.project import PagePlan, Project, ProjectFactRevision
from app.services.block_library import instantiate_kit_for_site
from app.services.claim_slots import resolve_claim_slot_bindings
from site_panel_blocks.schema import ThemeProfile
from site_panel_shared.manifests import PageManifest

GENERATOR_VERSION = "deterministic-v1"

_COMMERCIAL_FACT_TITLES = {
    "company_history": "О компании",
    "mission": "Миссия компании",
    "legal_entities": "Услуги для юридических лиц",
    "payment_terms": "Оплата и условия",
}


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
    design: dict | None = None,
    theme: ThemeProfile | None = None,
) -> tuple[dict, dict, str]:
    fact_values = facts.facts or {}
    service = _service_from_facts(fact_values)
    primary_geo = _primary_geo(plan)
    city = str(primary_geo.get("name") or "")
    blocks, _css_vars, kit_meta = instantiate_kit_for_site(
        plan.kit_key,
        str(project.id),
        service=service or "Услуги",
        theme=theme,
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
    claim_bindings = resolve_claim_slot_bindings(
        kit_key=plan.kit_key,
        block_ids=[block.type for block in blocks],
        block_selection=getattr(plan, "block_selection", None),
        facts=fact_values,
    )
    block_slot_values: dict[str, dict[str, str]] = {}
    claim_unique_core = next(
        (binding["claim"] for binding in claim_bindings if binding["slot"] == "unique_core"),
        None,
    )
    for binding in claim_bindings:
        if binding["slot"] != "unique_core":
            block_slot_values.setdefault(binding["block_id"], {})[binding["slot"]] = binding[
                "claim"
            ]
    title = f"{service} в {city_prep}".strip() if city else service
    commercial_fact_key = (getattr(plan, "source_refs", None) or {}).get("commercial_fact_key")
    commercial_copy = str(fact_values.get(commercial_fact_key) or "").strip()
    commercial_title = _COMMERCIAL_FACT_TITLES.get(commercial_fact_key or "")
    title_template = (
        f"{commercial_title} — {{domain}}"
        if commercial_title
        else "{service} в {city_prep} — {domain}"
    )
    h1_template = commercial_title or "{service} в {city_prep}"
    meta_description_template = (
        commercial_copy[:170] if commercial_title else "{service} в {city_prep}. Контакты: {phone}"
    )
    manifest = PageManifest(
        slug=plan.slug,
        title_template=title_template,
        h1_template=h1_template,
        meta_description_template=meta_description_template,
        service=service or "Услуги",
        geo_id=UUID(primary_geo["geo_id"]) if primary_geo.get("geo_id") else None,
        blocks=blocks,
        block_slot_values=block_slot_values,
        unique_core=claim_unique_core
        or commercial_copy
        or str(fact_values.get("unique_core") or ""),
        design=design,
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
        "claim_slot_bindings": [
            {
                "block_id": binding["block_id"],
                "slot": binding["slot"],
                "claim_index": binding["claim_index"],
            }
            for binding in claim_bindings
        ],
        "commercial_fact_key": commercial_fact_key,
        "design": design or {},
    }
    generator_meta = {
        "generator_version": GENERATOR_VERSION,
        "kit": kit_meta,
        "design_profile_hash": (design or {}).get("profile_hash"),
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
            *[
                value
                for block_values in manifest.block_slot_values.values()
                for value in block_values.values()
            ],
        ]
    )
    return (
        manifest.model_dump(mode="json"),
        {**input_snapshot, "generator_meta": generator_meta},
        content,
    )
