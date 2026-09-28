from __future__ import annotations

from typing import Any

from site_panel_blocks import block_slot_schema


def resolve_claim_slot_bindings(
    *,
    kit_key: str,
    block_ids: list[str],
    block_selection: dict[str, Any] | None,
    facts: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_bindings = (block_selection or {}).get("claim_slot_bindings") or []
    if not isinstance(raw_bindings, list) or len(raw_bindings) > 20:
        raise ValueError("Claim slot bindings are invalid")
    claims = facts.get("allowed_claims") or []
    if not isinstance(claims, list):
        raise ValueError("Confirmed claims are invalid")
    resolved: list[dict[str, Any]] = []
    targets: set[tuple[str, str]] = set()
    claim_indices: set[int] = set()
    for binding in raw_bindings:
        if not isinstance(binding, dict):
            raise ValueError("Claim slot bindings are invalid")
        block_id = binding.get("block_id")
        slot = binding.get("slot")
        claim_index = binding.get("claim_index")
        if (
            not isinstance(block_id, str)
            or not isinstance(slot, str)
            or not isinstance(claim_index, int)
            or block_id not in block_ids
        ):
            raise ValueError("Claim slot binding target is invalid")
        target = (block_id, slot)
        if target in targets:
            raise ValueError("Claim slot bindings must target unique block slots")
        if claim_index in claim_indices:
            raise ValueError("Claim slot bindings must reference unique confirmed claims")
        targets.add(target)
        claim_indices.add(claim_index)
        schema = block_slot_schema(kit_key, block_id)
        slot_spec = schema.get(slot)
        if not slot_spec:
            raise ValueError("Claim slot binding must target an editable curated text slot")
        if claim_index < 0 or claim_index >= len(claims):
            raise ValueError("Claim slot binding references an unavailable confirmed claim")
        claim = claims[claim_index]
        if not isinstance(claim, str) or not claim:
            raise ValueError("Claim slot binding references an invalid confirmed claim")
        if len(claim) > slot_spec["max_length"]:
            raise ValueError("Confirmed claim exceeds the curated slot length")
        resolved.append(
            {
                "block_id": block_id,
                "slot": slot,
                "claim_index": claim_index,
                "claim": claim,
            }
        )
    return resolved
