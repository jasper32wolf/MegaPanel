"""Lead anti-spam, qualification, webhook routing (TZ §9)."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from typing import Any

import httpx
from app.core.config import get_settings
from site_panel_security import BlindIndex, FieldEncryptor


def get_encryptor() -> FieldEncryptor:
    return FieldEncryptor.from_base64(get_settings().field_encryption_key)


def get_blind() -> BlindIndex:
    return BlindIndex(get_settings().blind_index_pepper)


def check_honeypot(website_field: str | None) -> bool:
    """Return True if spam (honeypot filled)."""
    return bool(website_field and website_field.strip())


def check_time_lock(
    form_ts: float,
    *,
    min_seconds: float = 2.5,
    max_seconds: float = 24 * 60 * 60,
    now: float | None = None,
) -> bool:
    """Return True when a form timestamp is invalid or suspicious."""
    submitted_at = time.time() if now is None else now
    age = submitted_at - form_ts
    return not math.isfinite(form_ts) or age < min_seconds or age > max_seconds


def qualify_lead_local(message: str | None, phone: str | None) -> str:
    """Micro qualification without LLM — spam heuristics."""
    text = (message or "").lower()
    spam_marks = ("seo продвижен", "купить ссылк", "crypto", "казино", "viagra", "партнерск")
    if any(m in text for m in spam_marks):
        return "spam"
    if phone and len(phone) >= 10:
        return "qualified"
    return "new"


def canonical_webhook_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sign_webhook(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(secret.encode(), canonical_webhook_body(payload), hashlib.sha256).hexdigest()


async def dispatch_webhook(url: str, payload: dict[str, Any], secret: str) -> dict:
    # Block SSRF to private nets; allow tenant CRM endpoints on public HTTPS
    from site_panel_security import SSRFBlockedError, SSRFGuard

    try:
        pinned = SSRFGuard().pin_url(url)
    except SSRFBlockedError as exc:
        return {"ok": False, "error": f"ssrf_blocked: {exc}"}
    body = canonical_webhook_body(payload)
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            request = client.build_request(
                "POST",
                pinned.transport_url,
                content=body,
                headers={
                    "Host": pinned.host_header,
                    "X-Signature-SHA256": signature,
                    "Content-Type": "application/json",
                    "Idempotency-Key": str(payload.get("idempotency_key", "")),
                },
                extensions={"sni_hostname": pinned.sni_hostname},
            )
            resp = await client.send(request)
            return {"status": resp.status_code, "ok": resp.status_code < 300}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# CRO checklist (TZ 9.2) aligned with block-library types
CRO_CHECKLISTS = {
    "hero": ["offer", "utp", "cta_above_fold", "trust_line"],
    "trust_bar": ["trust_line"],
    "services_grid": ["names", "prices_from", "sla", "guarantee"],
    "services": ["names", "prices_from", "sla", "guarantee"],
    "pricing_table": ["transparent", "compare", "cta_per_plan"],
    "pricing": ["transparent", "compare", "cta_per_plan"],
    "calculator": ["min_fields"],
    "process_steps": ["sla"],
    "team": ["trust_line"],
    "portfolio": ["trust_line"],
    "reviews": ["trust_line"],
    "guarantee": ["guarantee"],
    "faq": ["5_to_10_questions", "schema_faq", "no_water"],
    "cta_banner": ["cta_above_fold"],
    "lead_form": ["min_fields", "phone_mask", "pdn_consent", "alt_channels"],
    "contacts": ["alt_channels"],
    "footer": [],
}


def cro_score(page_type: str, present: list[str]) -> dict:
    required = CRO_CHECKLISTS.get(page_type, [])
    missing = [r for r in required if r not in present]
    score = int(100 * (1 - len(missing) / max(len(required), 1)))
    return {"score": score, "missing": missing, "required": required}
