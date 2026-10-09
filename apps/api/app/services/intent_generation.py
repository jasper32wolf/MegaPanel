"""Frozen, typed provider context for comprehensive intent-driven page proposals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models import PagePlan, Project, ProjectFactRevision
from app.schemas.ai import IntentPageProposalOut
from app.services.ai_data_policy import public_fact_rows, safe_provider_context
from app.services.design_profiles import design_snapshot, resolve_design_profile, theme_from_profile
from app.services.generation import create_page_draft
from site_panel_blocks import block_slot_schema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_CONTEXT_BYTES = 64_000
_FORBIDDEN_TEXT = ("<", ">", "{", "}", "javascript:", "http://", "https://")
_FORBIDDEN_VISIBILITY_TERMS = (
    "cloaking",
    "user-agent",
    "crawler",
    "bot-only",
    "aria-hidden",
    "display:none",
    "visibility:hidden",
)


@dataclass(frozen=True)
class IntentGenerationContext:
    provider_snapshot: dict[str, Any]
    source_binding: dict[str, Any]
    manifest: dict[str, Any]
    deterministic_snapshot: dict[str, Any]


def _plain_text(value: str, *, field: str) -> None:
    lowered = value.lower()
    if not value.strip() or any(token in lowered for token in _FORBIDDEN_TEXT):
        raise ValueError(f"Intent proposal {field} must be visible plain text")
    if any(token in lowered for token in _FORBIDDEN_VISIBILITY_TERMS):
        raise ValueError(f"Intent proposal {field} contains conditional or hidden-content behavior")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError(f"Intent proposal {field} contains control characters")


async def compile_intent_generation_context(
    db: AsyncSession,
    *,
    project: Project,
    plan: PagePlan,
) -> IntentGenerationContext:
    """Compile only immutable approved inputs; mutable project data never enters the envelope."""
    if plan.state != "approved" or not plan.fact_revision_id:
        raise ValueError("Approve the PagePlan before intent generation")
    facts = (
        await db.execute(
            select(ProjectFactRevision).where(
                ProjectFactRevision.id == plan.fact_revision_id,
                ProjectFactRevision.project_id == project.id,
                ProjectFactRevision.tenant_id == project.tenant_id,
                ProjectFactRevision.state == "confirmed",
            )
        )
    ).scalar_one_or_none()
    if not facts:
        raise ValueError("PagePlan confirmed facts are unavailable")
    resolved = await resolve_design_profile(db, tenant_id=project.tenant_id, project_id=project.id)
    profile_snapshot = design_snapshot(resolved)
    if not profile_snapshot or not resolved.effective_profile:
        raise ValueError("Approve a design profile before intent generation")
    if plan.kit_key not in resolved.effective_profile.layout.allowed_kits:
        raise ValueError("Approved PagePlan kit is not allowed by the design profile")
    manifest, deterministic_snapshot, _ = create_page_draft(
        project=project,
        plan=plan,
        facts=facts,
        design=profile_snapshot,
        theme=theme_from_profile(resolved.effective_profile),
    )
    selected_block_ids = {item["type"] for item in manifest["blocks"]}
    required_blocks = set(resolved.effective_profile.layout.required_blocks)
    missing_required_blocks = required_blocks - selected_block_ids
    if missing_required_blocks:
        raise ValueError("Approved PagePlan omits required design-profile blocks")
    allowed_blocks = []
    for block in manifest["blocks"]:
        allowed_blocks.append(
            {
                "id": block["type"],
                "slots": block_slot_schema(plan.kit_key, block["type"]),
            }
        )
    snapshot = {
        "approved_page_plan": {
            "id": str(plan.id),
            "version": plan.version,
            "slug": plan.slug,
            "objective": plan.objective,
            "intent": plan.intent,
            "kit_key": plan.kit_key,
        },
        "frozen_sources": {
            "fact_revision_id": str(facts.id),
            "facts_hash": facts.facts_hash,
            "keyword_snapshot": plan.keyword_snapshot or {},
            "geo_snapshot": plan.geo_snapshot or {},
            "semantic_target": plan.semantic_target_snapshot or {},
            "structure": (plan.source_refs or {}).get("site_structure"),
        },
        "confirmed_facts": public_fact_rows(facts.facts or {}),
        "selected_keywords": (plan.keyword_snapshot or {}).get("items", []),
        "validated_geo": (plan.geo_snapshot or {}).get("items", []),
        "allowed_blocks": allowed_blocks,
        "resolved_design_profile": {
            "hash": profile_snapshot["profile_hash"],
            "site_family": resolved.effective_profile.site_family,
            "layout": resolved.effective_profile.layout.model_dump(mode="json"),
            "art_direction": profile_snapshot["art_direction"],
        },
        "policy": {"noindex_default": True, "all_output_visible": True},
    }
    try:
        safe_snapshot = safe_provider_context(snapshot)
    except ValueError as exc:
        raise ValueError("Intent generation context is unsafe") from exc
    if len(json.dumps(safe_snapshot, ensure_ascii=False).encode("utf-8")) > _MAX_CONTEXT_BYTES:
        raise ValueError("Intent generation context exceeds the request limit")
    return IntentGenerationContext(
        provider_snapshot=safe_snapshot,
        source_binding={
            "page_plan_id": str(plan.id),
            "plan_version": plan.version,
            "fact_revision_id": str(facts.id),
            "facts_hash": facts.facts_hash,
            "semantic_target_snapshot": plan.semantic_target_snapshot or {},
            "design_profile_hash": profile_snapshot["profile_hash"],
            "design_profile_revision_id": profile_snapshot["profile_revision_id"],
            "kit_key": plan.kit_key,
        },
        manifest=manifest,
        deterministic_snapshot=deterministic_snapshot,
    )


def validate_intent_page_proposal(
    value: dict[str, Any],
    *,
    source_binding: dict[str, Any],
    allowed_block_slots: dict[str, dict[str, dict[str, Any]]],
    fact_keys: set[str],
    semantic_project_keyword_ids: set[str],
) -> dict[str, Any]:
    """Reject any model output outside the frozen page/design contract."""
    proposal = IntentPageProposalOut.model_validate(value)
    if str(proposal.page_plan_id) != source_binding["page_plan_id"]:
        raise ValueError("Intent proposal targets a different PagePlan")
    if proposal.plan_version != source_binding["plan_version"]:
        raise ValueError("Intent proposal targets a stale PagePlan version")
    if any(key not in fact_keys for key in proposal.fact_keys):
        raise ValueError("Intent proposal references an unknown business fact")
    if any(
        str(item) not in semantic_project_keyword_ids
        for item in proposal.semantic_target_project_keyword_ids
    ):
        raise ValueError("Intent proposal references an unselected semantic target")
    for field in (proposal.title, proposal.h1, proposal.meta_description, proposal.unique_core):
        _plain_text(field, field="copy")
    for block_id, slots in proposal.block_slots.items():
        schema = allowed_block_slots.get(block_id)
        if schema is None:
            raise ValueError("Intent proposal selected an unknown block")
        if not set(slots).issubset(schema):
            raise ValueError("Intent proposal selected an unsupported block slot")
        for slot_name, text in slots.items():
            if text is not None:
                _plain_text(text, field=f"block slot {block_id}.{slot_name}")
                if len(text) > int(schema[slot_name]["max_length"]):
                    raise ValueError("Intent proposal exceeds a block slot length")
    for section in proposal.section_rationale:
        if section.block_id not in allowed_block_slots:
            raise ValueError("Intent proposal rationale selected an unknown block")
        _plain_text(section.visitor_question, field="section rationale")
        for topic in section.semantic_topics:
            _plain_text(topic, field="semantic topic")
    for text in [
        proposal.art_direction.creative_direction,
        *proposal.art_direction.shot_list,
        *proposal.art_direction.alt_requirements,
        *proposal.warnings,
    ]:
        _plain_text(text, field="art direction")
    return proposal.model_dump(mode="json")


__all__ = [
    "IntentGenerationContext",
    "compile_intent_generation_context",
    "validate_intent_page_proposal",
]
