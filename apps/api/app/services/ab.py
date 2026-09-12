"""Simple A/B assignment by visitor seed (TZ 9.3)."""

from __future__ import annotations

import hashlib


def assign_variant(visitor_id: str, experiment: str, variants: list[str]) -> str:
    if not variants:
        raise ValueError("variants required")
    digest = hashlib.sha256(f"{experiment}:{visitor_id}".encode()).hexdigest()
    idx = int(digest[:8], 16) % len(variants)
    return variants[idx]


def significant(conversions_a: int, visitors_a: int, conversions_b: int, visitors_b: int) -> bool:
    """Very rough early-stop heuristic — replace with real stats later."""
    if visitors_a < 100 or visitors_b < 100:
        return False
    rate_a = conversions_a / visitors_a
    rate_b = conversions_b / visitors_b
    return abs(rate_a - rate_b) > 0.05 and min(visitors_a, visitors_b) >= 200
