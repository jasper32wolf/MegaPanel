from __future__ import annotations

import re

from app.services.claim_slots import resolve_claim_slot_bindings
from app.services.dedup import compare_texts
from site_panel_shared.manifests import PageManifest

_HIDDEN_OR_CONDITIONAL_CONTENT = re.compile(
    r"(?i)(display\s*:\s*none|visibility\s*:\s*hidden|aria-hidden|user-agent|crawler|cloaking)"
)


def _public_schema_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_public_schema_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_public_schema_text(item) for item in value)
    return ""


def run_page_qa(*, page_manifest: dict, input_snapshot: dict, existing_texts: list[str]) -> dict:
    page = PageManifest.model_validate(page_manifest)
    facts = input_snapshot.get("facts") or {}
    keywords = (input_snapshot.get("keyword_snapshot") or {}).get("items") or []
    findings: list[dict[str, str]] = []
    service = str(facts.get("service") or "").strip()
    services = facts.get("services") or []
    if not service and not any(isinstance(item, str) and item.strip() for item in services):
        findings.append(
            {
                "verdict": "block",
                "rule": "required_service",
                "evidence": "Confirm a service in business facts",
            }
        )
    contacts = facts.get("contacts") or {}
    if not isinstance(contacts, dict) or not str(contacts.get("phone") or "").strip():
        findings.append(
            {
                "verdict": "block",
                "rule": "required_contact",
                "evidence": "Confirm a phone in business facts",
            }
        )
    if not input_snapshot.get("project_domain"):
        findings.append(
            {"verdict": "block", "rule": "required_domain", "evidence": "Set a project domain"}
        )
    if not (input_snapshot.get("geo_snapshot") or {}).get("items"):
        findings.append(
            {
                "verdict": "block",
                "rule": "required_geo",
                "evidence": "Select a primary geographic place",
            }
        )
    if not page.slug.startswith("/") or "//" in page.slug:
        findings.append(
            {"verdict": "block", "rule": "safe_slug", "evidence": "Use a normalized unique slug"}
        )
    ai_provenance = input_snapshot.get("ai_provenance") or {}
    if ai_provenance:
        required = ("provider_id", "model_id", "prompt_id", "prompt_version", "prompt_hash")
        missing = [field for field in required if not ai_provenance.get(field)]
        if missing:
            findings.append(
                {
                    "verdict": "block",
                    "rule": "ai_provenance",
                    "evidence": f"AI provenance is missing: {', '.join(missing)}",
                }
            )
        fact_keys = set(ai_provenance.get("fact_keys") or [])
        known_fact_keys = set(facts) if isinstance(facts, dict) else set()
        unknown_fact_keys = sorted(fact_keys - known_fact_keys)
        if unknown_fact_keys:
            findings.append(
                {
                    "verdict": "block",
                    "rule": "ai_fact_provenance",
                    "evidence": f"AI copy cites unknown facts: {', '.join(unknown_fact_keys)}",
                }
            )
        if page.index_state != "noindex":
            findings.append(
                {
                    "verdict": "block",
                    "rule": "ai_index_policy",
                    "evidence": (
                        "AI-generated content remains noindex until an approved "
                        "indexing policy applies"
                    ),
                }
            )
    design_snapshot = input_snapshot.get("design") or {}
    if page.design and page.design.profile_hash != design_snapshot.get("profile_hash"):
        findings.append(
            {
                "verdict": "block",
                "rule": "design_profile_provenance",
                "evidence": "Page design profile differs from the frozen draft snapshot",
            }
        )
    if ai_provenance and not page.design:
        findings.append(
            {
                "verdict": "warn",
                "rule": "design_profile_missing",
                "evidence": "AI draft has no resolved approved design profile snapshot",
            }
        )
    intent_generation = input_snapshot.get("intent_generation") or {}
    semantic_targets = (
        (input_snapshot.get("semantic_target_snapshot") or {}).get("targets") or []
    )
    expected_target_ids = {
        str(item.get("project_keyword_id"))
        for item in semantic_targets
        if isinstance(item, dict) and item.get("project_keyword_id")
    }
    actual_target_ids = {
        str(item)
        for item in intent_generation.get("semantic_target_project_keyword_ids") or []
    }
    if expected_target_ids and not expected_target_ids.issubset(actual_target_ids):
        findings.append(
            {
                "verdict": "warn",
                "rule": "intent_semantic_coverage",
                "evidence": "AI proposal did not cover every frozen semantic target",
            }
        )
    claim_slot_bindings = input_snapshot.get("claim_slot_bindings") or []
    if claim_slot_bindings:
        try:
            resolved_claim_bindings = resolve_claim_slot_bindings(
                kit_key=str(input_snapshot.get("kit_key") or ""),
                block_ids=[block.type for block in page.blocks],
                block_selection={"claim_slot_bindings": claim_slot_bindings},
                facts=facts,
            )
            for binding in resolved_claim_bindings:
                actual = (
                    page.unique_core
                    if binding["slot"] == "unique_core"
                    else (page.block_slot_values.get(binding["block_id"]) or {}).get(
                        binding["slot"]
                    )
                )
                if actual != binding["claim"]:
                    raise ValueError("Bound curated slot does not match the frozen confirmed claim")
        except (FileNotFoundError, ValueError) as exc:
            findings.append(
                {
                    "verdict": "block",
                    "rule": "claim_slot_contract",
                    "evidence": str(exc),
                }
            )
    block_slot_text = " ".join(
        str(value or "")
        for block_values in page.block_slot_values.values()
        for value in block_values.values()
    )
    media_alt_text = " ".join(
        attachment.alt for attachment in [*page.media, *page.block_media.values()]
    )
    rendered_text = " ".join(
        [
            page.title_template,
            page.h1_template,
            page.meta_description_template,
            page.service,
            page.unique_core or "",
            block_slot_text,
            media_alt_text,
            _public_schema_text(page.schema_org),
        ]
    )
    if _HIDDEN_OR_CONDITIONAL_CONTENT.search(rendered_text):
        findings.append(
            {
                "verdict": "block",
                "rule": "visible_content_integrity",
                "evidence": "Generated page text contains hidden or conditional-content markers",
            }
        )
    if re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", rendered_text):
        findings.append(
            {
                "verdict": "block",
                "rule": "public_email_leak",
                "evidence": "Public page fields must not contain an email address",
            }
        )
    if any(
        compare_texts(rendered_text, existing) > 0.85 for existing in existing_texts if existing
    ):
        findings.append(
            {
                "verdict": "block",
                "rule": "duplicate_content",
                "evidence": "Candidate is too similar to an existing draft",
            }
        )
    if not keywords:
        findings.append(
            {
                "verdict": "warn",
                "rule": "keyword_coverage",
                "evidence": "No approved keywords are linked to this page",
            }
        )
    if len(page.title_template) > 70:
        findings.append(
            {
                "verdict": "warn",
                "rule": "title_length",
                "evidence": "Title template exceeds 70 characters",
            }
        )
    if len(page.meta_description_template) > 170:
        findings.append(
            {
                "verdict": "warn",
                "rule": "meta_length",
                "evidence": "Meta description exceeds 170 characters",
            }
        )
    if len(rendered_text) < 120:
        findings.append(
            {
                "verdict": "warn",
                "rule": "thin_content",
                "evidence": "Candidate needs more approved content before publication",
            }
        )
    verdict = (
        "block"
        if any(item["verdict"] == "block" for item in findings)
        else "warn"
        if findings
        else "pass"
    )
    return {"verdict": verdict, "findings": findings}


__all__ = ["run_page_qa"]
