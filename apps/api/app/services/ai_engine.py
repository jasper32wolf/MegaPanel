from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class MicroInfillOut(BaseModel):
    """Compact JSON Schema for Micro-Infill (TZ 5.1) — 50–100 token unique core."""

    unique_core: str = Field(min_length=20, max_length=400)
    local_theses: list[str] = Field(min_length=2, max_length=5)
    offer: str = Field(min_length=10, max_length=200)


DEFAULT_MICRO_PROMPT = """Ты генерируешь только JSON по схеме.
Ниша: {niche}. Услуга: {service}. Город: {city}.
Верни unique_core (1-2 предложения), local_theses (2-3), offer.
Без HTML. Без PII.
"""

_INJECTION = re.compile(
    r"(ignore\s+previous|system\s*prompt|<\/?script|\{%|%\})",
    re.I,
)


def redact_pii(text: str) -> str:
    text = re.sub(r"\+?\d[\d\-\s\(\)]{8,}\d", "[phone]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text)
    return text


def guard_prompt_input(text: str) -> str:
    if _INJECTION.search(text):
        raise ValueError("Prompt injection pattern detected")
    return redact_pii(text)


def cache_key(prompt: str, model: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"p": prompt, "m": model, "x": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def local_micro_infill(context: dict[str, str]) -> MicroInfillOut:
    """Zero-token fallback: combinatorial template (ultra-economy path)."""
    service = context.get("service", "услуги")
    city = context.get("city", "городе")
    city_prep = context.get("city_prep", city)
    niche = context.get("niche", "сервис")
    cores = [
        f"Локальная команда выполняет {service} в {city_prep} с выездом в день обращения.",
        f"Для жителей {city_prep}: {service} под ключ, прозрачная смета и гарантия на работы.",
        f"{service.capitalize()} в {city_prep} — акцент на сроки, аккуратность и понятную цену.",
    ]
    seed = abs(hash(f"{niche}:{service}:{city}")) % len(cores)
    return MicroInfillOut(
        unique_core=cores[seed],
        local_theses=[
            f"Работаем по {city_prep} и ближайшим районам",
            f"Фиксируем объём до начала {service}",
            "Оставляем контакты ответственного мастера",
        ],
        offer=f"{service.capitalize()} в {city_prep} — расчёт за 15 минут",
    )


def validate_infill(data: dict[str, Any]) -> MicroInfillOut:
    try:
        return MicroInfillOut.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid micro-infill JSON: {exc}") from exc


def slot_fill_sentences(seeds: list[str], synonyms: dict[str, list[str]], context: dict[str, str]) -> list[str]:
    """Combinatorial mutation without LLM (TZ 5.1)."""
    out: list[str] = []
    for seed in seeds:
        variants = [seed]
        for key, alts in synonyms.items():
            token = "{" + key + "}"
            if token not in seed:
                continue
            expanded: list[str] = []
            for base in variants:
                for alt in alts:
                    expanded.append(base.replace(token, alt))
            variants = expanded
        for v in variants:
            filled = v
            for ck, cv in context.items():
                filled = filled.replace("{" + ck + "}", str(cv))
            out.append(filled)
    return out
