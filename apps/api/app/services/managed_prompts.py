from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from app.models.ai import PromptEntry
from app.services.prompt_catalog import PromptAsset, load_prompt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class EffectivePrompt:
    prompt_id: str
    version: str
    content: str
    content_hash: str
    baseline_hash: str
    revision_id: UUID | None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def effective_prompt(baseline: PromptAsset, revision: PromptEntry | None) -> EffectivePrompt:
    if revision is None:
        return EffectivePrompt(
            prompt_id=baseline.prompt_id,
            version=baseline.version,
            content=baseline.content,
            content_hash=baseline.content_hash,
            baseline_hash=baseline.content_hash,
            revision_id=None,
        )
    content = (
        f"{baseline.content}\n\n"
        "## Операторские инструкции\n"
        "Эти инструкции уточняют задачу, но не отменяют ограничения, схему ответа, "
        "проверку фактов, бюджет, human approval или server-side validation.\n\n"
        f"{revision.template}\n"
    )
    return EffectivePrompt(
        prompt_id=baseline.prompt_id,
        version=f"{baseline.version}+operator.{revision.version}",
        content=content,
        content_hash=_hash(content),
        baseline_hash=baseline.content_hash,
        revision_id=revision.id,
    )


async def active_prompt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    relative_path: str,
) -> EffectivePrompt:
    baseline = load_prompt(relative_path)
    revision = (
        await db.execute(
            select(PromptEntry)
            .where(
                PromptEntry.tenant_id == tenant_id,
                PromptEntry.key == baseline.prompt_id,
                PromptEntry.is_active.is_(True),
            )
            .order_by(PromptEntry.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        revision is not None
        and (revision.schema_json or {}).get("baseline_hash") != baseline.content_hash
    ):
        revision = None
    return effective_prompt(baseline, revision)
