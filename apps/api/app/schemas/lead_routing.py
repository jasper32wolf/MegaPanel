from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

_TARGET_KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class LeadRoutingDestinationIn(BaseModel):
    target_key: str = Field(min_length=1, max_length=64)
    channel: Literal["email", "webhook"]
    required: bool = True
    recipient: EmailStr | None = None
    webhook_url: str | None = Field(default=None, min_length=8, max_length=2048)
    webhook_secret: str | None = Field(default=None, min_length=16, max_length=512)

    @field_validator("target_key")
    @classmethod
    def validate_target_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _TARGET_KEY.fullmatch(normalized):
            raise ValueError(
                "Target key must use lowercase letters, digits, hyphens and underscores"
            )
        return normalized

    @model_validator(mode="after")
    def validate_channel_fields(self) -> LeadRoutingDestinationIn:
        if self.channel == "email":
            if not self.recipient or self.webhook_url or self.webhook_secret:
                raise ValueError("Email destinations require only a recipient")
        elif not self.webhook_url or not self.webhook_secret or self.recipient:
            raise ValueError("Webhook destinations require only a URL and secret")
        return self


class LeadRoutingPolicyCreate(BaseModel):
    destinations: list[LeadRoutingDestinationIn] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def require_unique_target_keys(self) -> LeadRoutingPolicyCreate:
        keys = [destination.target_key for destination in self.destinations]
        if len(keys) != len(set(keys)):
            raise ValueError("Routing destination keys must be unique")
        return self


class LeadRoutingDecisionIn(BaseModel):
    confirmed: bool
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_confirmation(self) -> LeadRoutingDecisionIn:
        if not self.confirmed:
            raise ValueError("Explicit confirmation is required")
        return self
