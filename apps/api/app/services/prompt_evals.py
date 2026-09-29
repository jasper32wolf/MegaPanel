from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.prompt_catalog import PromptAsset

_EVALUATION_FIXTURE = re.compile(r"^- \*\*Evaluation fixtures:\*\* `([^`]+)`$", re.MULTILINE)


def prompt_evaluation_fixture(prompt: PromptAsset) -> Path:
    matches = _EVALUATION_FIXTURE.findall(prompt.content)
    if len(matches) != 1:
        raise ValueError("Packaged prompt must declare exactly one evaluation fixture")
    fixture = (prompt.path.parent / matches[0]).resolve()
    evals_root = Path(__file__).resolve().parents[1] / "prompts" / "ai" / "evals"
    evals_root = evals_root.resolve()
    if fixture.suffix != ".jsonl" or evals_root not in fixture.parents:
        raise ValueError("Prompt evaluation fixture is outside the canonical eval directory")
    return fixture


def validate_prompt_evaluation_fixture(prompt: PromptAsset) -> Path:
    fixture = prompt_evaluation_fixture(prompt)
    try:
        lines = fixture.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError("Packaged prompt evaluation fixture is missing") from exc
    if not lines:
        raise ValueError("Packaged prompt evaluation fixture is empty")
    for line_number, line in enumerate(lines, start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Prompt evaluation fixture line {line_number} is invalid JSON"
            ) from exc
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
            or not isinstance(item.get("input"), dict)
            or not isinstance(item.get("assert"), dict)
        ):
            raise ValueError(
                f"Prompt evaluation fixture line {line_number} has an invalid contract"
            )
    return fixture
