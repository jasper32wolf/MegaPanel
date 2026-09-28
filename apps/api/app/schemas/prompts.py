from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_UNSAFE_PROMPT_TEXT = re.compile(
    r"(?:api[_ -]?key|password|secret|bearer\s+|access[_ -]?token|"
    r"private[_ -]?lead|lead[_ -]?email|publish|rollback|restore|deploy|"
    r"bypass|disable\s+(?:approval|validation|budget)|"
    r"<\/?(?:system|developer|tool)>|\{\{.*\}\})",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?:\+7|8)[\s()-]?\d[\d\s()-]{8,}")


class PromptRevisionCreate(BaseModel):
    instructions: str = Field(min_length=1, max_length=12_000)

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Provide prompt instructions")
        if (
            _UNSAFE_PROMPT_TEXT.search(normalized)
            or _EMAIL.search(normalized)
            or _PHONE.search(normalized)
        ):
            raise ValueError(
                "Prompt instructions contain a prohibited secret, PII, or workflow override"
            )
        return normalized
