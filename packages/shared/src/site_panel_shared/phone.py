from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def normalize_phone_e164(value: str | None) -> str | None:
    """Return a conservative Russian E.164 value when the source is unambiguous."""
    if not value:
        return None
    digits = _DIGITS.sub("", value)
    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    return None


def format_phone_display(value: str | None) -> str:
    """Format unambiguous Russian phones for people without changing stored facts."""
    normalized = normalize_phone_e164(value)
    if not normalized:
        return (value or "").strip()
    national = normalized[2:]
    return f"8({national[:3]}){national[3:6]}-{national[6:8]}-{national[8:]}"
