from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

_CASE_MAP = {
    "nomn": "nom",
    "gent": "gen",
    "datv": "dat",
    "accs": "acc",
    "ablt": "ins",
    "loct": "prep",
}


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


def word_hash(word: str) -> str:
    return hashlib.sha256(word.strip().lower().encode("utf-8")).hexdigest()


def inflect_cases(word: str) -> dict[str, str]:
    """Return Russian case forms for a toponym/noun."""
    morph = _morph()
    parsed = morph.parse(word)
    if not parsed:
        return {"nom": word, "gen": word, "dat": word, "acc": word, "ins": word, "prep": word}
    p = parsed[0]
    forms: dict[str, str] = {"nom": p.normal_form if p.tag.POS in {"NOUN", "ADJF"} else word}
    # Prefer original nominative surface for display
    forms["nom"] = word
    for gram, key in _CASE_MAP.items():
        if key == "nom":
            continue
        inflected = p.inflect({gram})
        forms[key] = inflected.word if inflected else word
    # Capitalize like original if it was a proper name
    if word[:1].isupper():
        forms = {k: v[:1].upper() + v[1:] if v else v for k, v in forms.items()}
    return forms


def city_placeholders(name: str, forms: dict[str, str] | None = None) -> dict[str, str]:
    f = forms or inflect_cases(name)
    return {
        "city": f.get("nom", name),
        "city_nom": f.get("nom", name),
        "city_gen": f.get("gen", name),
        "city_dat": f.get("dat", name),
        "city_acc": f.get("acc", name),
        "city_ins": f.get("ins", name),
        "city_prep": f.get("prep", name),
    }


def validate_agreement(template: str, context: dict[str, Any]) -> list[str]:
    """Lightweight check: required city_* placeholders must be non-empty if present in template."""
    import re

    issues: list[str] = []
    for key in re.findall(r"\{([a-z0-9_]+)\}", template, flags=re.I):
        if key.startswith("city_") and not context.get(key):
            issues.append(f"missing:{key}")
    return issues
