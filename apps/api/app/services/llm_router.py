"""LLM failover router: DeepSeek → Anthropic → OpenAI-compatible → local (TZ 5.1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from app.core.config import get_settings
from app.services.ai_engine import MicroInfillOut, local_micro_infill, validate_infill
from app.services.hardening import egress, finops


@dataclass
class LlmResult:
    data: MicroInfillOut
    model: str
    tokens_in: int
    tokens_out: int
    provider: str
    cached_local: bool = False


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    egress.assert_allowed(url)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def call_deepseek(prompt: str) -> LlmResult:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    url = "https://api.deepseek.com/chat/completions"
    data = await _post_json(
        url,
        {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        {
            "model": settings.llm_micro_model_deepseek,
            "messages": [
                {"role": "system", "content": "Отвечай только валидным JSON без markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        },
    )
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return LlmResult(
        data=validate_infill(_extract_json(content)),
        model=settings.llm_micro_model_deepseek,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        provider="deepseek",
    )


async def call_anthropic(prompt: str) -> LlmResult:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    url = "https://api.anthropic.com/v1/messages"
    data = await _post_json(
        url,
        {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        {
            "model": settings.llm_micro_model_anthropic,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt + "\nОтвет — только JSON."}],
        },
    )
    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    usage = data.get("usage") or {}
    return LlmResult(
        data=validate_infill(_extract_json(text)),
        model=settings.llm_micro_model_anthropic,
        tokens_in=int(usage.get("input_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or 0),
        provider="anthropic",
    )


async def call_openai_compatible(prompt: str) -> LlmResult:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    base = settings.openai_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    data = await _post_json(
        url,
        {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        {
            "model": settings.llm_micro_model_openai,
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
        },
    )
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return LlmResult(
        data=validate_infill(_extract_json(content)),
        model=settings.llm_micro_model_openai,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        provider="openai_compatible",
    )


async def generate_micro_infill(
    prompt: str,
    context: dict[str, str],
    *,
    use_llm: bool,
    tenant_id: str,
) -> LlmResult:
    """
    Failover: DeepSeek → Anthropic → OpenAI-compatible → local slot-fill.
    Always falls back to local on any error (ultra-economy safe path).
    """
    if not use_llm:
        out = local_micro_infill(context)
        return LlmResult(
            data=out,
            model="local-slotfill",
            tokens_in=0,
            tokens_out=0,
            provider="local",
            cached_local=True,
        )

    errors: list[str] = []
    for name, fn in (
        ("deepseek", call_deepseek),
        ("anthropic", call_anthropic),
        ("openai", call_openai_compatible),
    ):
        try:
            result = await fn(prompt)
            # rough FinOps: $0.14 / 1M in + $0.28 / 1M out as placeholder micro pricing
            cost = (result.tokens_in * 0.14 + result.tokens_out * 0.28) / 1_000_000
            finops.add(tenant_id, "llm", cost, {"provider": result.provider, "model": result.model})
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue

    out = local_micro_infill(context)
    finops.add(tenant_id, "llm", 0.0, {"provider": "local_fallback", "errors": errors[:3]})
    return LlmResult(
        data=out,
        model="local-slotfill",
        tokens_in=0,
        tokens_out=0,
        provider="local_fallback",
        cached_local=True,
    )
