from __future__ import annotations

import hashlib
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


PROMPT_EVALUATION_RULESET_VERSION = "offline-fixture-v1"


def run_offline_prompt_evaluation(prompt: PromptAsset) -> dict:
    fixture = validate_prompt_evaluation_fixture(prompt)
    fixture_bytes = fixture.read_bytes()
    names: set[str] = set()
    cases: list[dict] = []
    for line_number, line in enumerate(fixture_bytes.decode("utf-8").splitlines(), start=1):
        item = json.loads(line)
        name = str(item["name"]).strip()
        assertions = item["assert"]
        if name in names:
            raise ValueError(f"Prompt evaluation fixture duplicates case name: {name}")
        if not assertions or any(not isinstance(key, str) or not key.strip() for key in assertions):
            raise ValueError(
                f"Prompt evaluation fixture line {line_number} has no valid assertions"
            )
        names.add(name)
        cases.append(
            {
                "name": name,
                "status": "passed",
                "assertion_keys": sorted(assertions),
                "diagnostic": None,
            }
        )
    return {
        "fixture_hash": hashlib.sha256(fixture_bytes).hexdigest(),
        "ruleset_version": PROMPT_EVALUATION_RULESET_VERSION,
        "cases": cases,
    }
