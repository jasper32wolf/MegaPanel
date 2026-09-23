from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptAsset:
    prompt_id: str
    version: str
    path: Path
    content: str
    content_hash: str


_HEADER = re.compile(r"^- \*\*(ID|Version):\*\* `([^`]+)`$", re.MULTILINE)


def _prompt_root() -> Path:
    return Path(__file__).resolve().parents[1] / "prompts" / "ai"


def load_prompt(relative_path: str) -> PromptAsset:
    root = _prompt_root().resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents or path.suffix != ".md":
        raise ValueError("Prompt path is outside the canonical prompt directory")
    content = path.read_text(encoding="utf-8")
    fields = dict(_HEADER.findall(content))
    if "ID" not in fields or "Version" not in fields:
        raise ValueError(f"Prompt metadata is incomplete: {relative_path}")
    return PromptAsset(
        prompt_id=fields["ID"],
        version=fields["Version"],
        path=path,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def list_prompts() -> list[PromptAsset]:
    return [
        load_prompt(str(path.relative_to(_prompt_root())))
        for path in sorted(_prompt_root().glob("**/*.md"))
    ]


__all__ = ["PromptAsset", "list_prompts", "load_prompt"]
