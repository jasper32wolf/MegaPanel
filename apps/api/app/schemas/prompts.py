from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PromptRevisionCreate(BaseModel):
    instructions: str = Field(min_length=1, max_length=12_000)

    @field_validator("instructions")
    @classmethod
    def strip_instructions(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Provide prompt instructions")
        return normalized
