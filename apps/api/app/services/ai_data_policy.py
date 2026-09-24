from __future__ import annotations

import re
from typing import Any

from app.services.ai_engine import redact_pii

_CREDENTIAL = re.compile(
    r"(?:\b(?:api[_ -]?key|password|secret|token)\s*[:=]|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,})",
    re.IGNORECASE,
)
_SENSITIVE_KEY = re.compile(
    r"^(?:api[_-]?key|password|secret|token|access[_-]?token|email|phone|lead[_-]?id)$",
    re.IGNORECASE,
)


def public_fact_rows(facts: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    service = facts.get("service")
    if isinstance(service, str) and service.strip():
        rows.append({"fact_key": "service", "value": service.strip()})
    services = facts.get("services")
    if isinstance(services, list):
        for item in services[:30]:
            if isinstance(item, str) and item.strip():
                rows.append({"fact_key": "services", "value": item.strip()})
    return rows


def safe_provider_context(value: Any) -> Any:
    if isinstance(value, str):
        if _CREDENTIAL.search(value):
            raise ValueError("External AI context contains credential-like data")
        return redact_pii(value)
    if isinstance(value, list):
        return [safe_provider_context(item) for item in value]
    if isinstance(value, dict):
        if any(_SENSITIVE_KEY.fullmatch(str(key)) for key in value):
            raise ValueError("External AI context contains sensitive field names")
        return {key: safe_provider_context(item) for key, item in value.items()}
    return value
