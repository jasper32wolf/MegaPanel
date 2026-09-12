from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models.ai import ContentHash, DeadLetterJob, GenerationJob, LlmCache, PromptEntry
from app.services.ai_engine import (
    DEFAULT_MICRO_PROMPT,
    MicroInfillOut,
    cache_key,
    guard_prompt_input,
    local_micro_infill,
    slot_fill_sentences,
    validate_infill,
)
from app.services.audit import append_audit
from app.services.dedup import compare_texts, hamming, minhash_buckets, similarity_from_hamming, simhash64
from app.services.llm_router import generate_micro_infill

router = APIRouter()


class GenerateRequest(BaseModel):
    niche: str
    service: str
    city: str
    city_prep: str | None = None
    site_id: UUID | None = None
    use_llm: bool = False
    page_id: str = "page-1"


class GenerateOut(BaseModel):
    job_id: UUID
    status: str
    output: dict
    tokens_in: int
    tokens_out: int
    dedup_similarity: float | None = None
    blocked: bool = False
    provider: str | None = None
    model: str | None = None


class DedupCheck(BaseModel):
    text_a: str
    text_b: str


class MutateBody(BaseModel):
    seeds: list[str]
    synonyms: dict[str, list[str]] = Field(default_factory=dict)
    context: dict[str, str] = Field(default_factory=dict)


@router.post("/micro-infill", response_model=GenerateOut)
async def micro_infill(
    body: GenerateRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> GenerateOut:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")

    ctx = {
        "niche": guard_prompt_input(body.niche),
        "service": guard_prompt_input(body.service),
        "city": guard_prompt_input(body.city),
        "city_prep": guard_prompt_input(body.city_prep or body.city),
    }
    job = GenerationJob(
        tenant_id=auth.tenant_id,
        site_id=body.site_id,
        status="running",
        model_tier="micro",
        prompt_key="micro_infill",
        input=ctx,
    )
    db.add(job)
    await db.flush()

    try:
        prompt = DEFAULT_MICRO_PROMPT.format(**ctx)
        model_name = "llm-tiered" if body.use_llm else "local-slotfill"
        key = cache_key(prompt, model_name if body.use_llm else "local-slotfill", {**ctx, "use_llm": body.use_llm})
        cached = await db.execute(select(LlmCache).where(LlmCache.cache_key == key))
        hit = cached.scalar_one_or_none()
        provider = "cache"
        model_used = model_name
        if hit:
            out = validate_infill(hit.response)
            tokens_in = tokens_out = 0
        else:
            llm = await generate_micro_infill(
                prompt,
                ctx,
                use_llm=body.use_llm,
                tenant_id=str(auth.tenant_id),
            )
            out = llm.data
            tokens_in, tokens_out = llm.tokens_in, llm.tokens_out
            provider, model_used = llm.provider, llm.model
            db.add(LlmCache(cache_key=key, response=out.model_dump(), model=model_used))
            if body.use_llm and (tokens_in or tokens_out):
                from app.services.hardening import record_finops

                cost = (tokens_in * 0.14 + tokens_out * 0.28) / 1_000_000
                await record_finops(
                    db,
                    auth.tenant_id,
                    "llm",
                    cost,
                    {"provider": provider, "model": model_used},
                )

        text_blob = " ".join([out.unique_core, out.offer, *out.local_theses])
        existing = await db.execute(select(ContentHash).where(ContentHash.tenant_id == auth.tenant_id))
        max_sim = 0.0
        for row in existing.scalars().all():
            sim = similarity_from_hamming(hamming(row.simhash, simhash64(text_blob)))
            max_sim = max(max_sim, sim)

        blocked = max_sim > 0.85
        if 0.70 < max_sim <= 0.85:
            out = local_micro_infill({**ctx, "service": f"{body.service} экспресс"})
            text_blob = " ".join([out.unique_core, out.offer, *out.local_theses])
            provider = f"{provider}+regen"

        if blocked:
            db.add(
                DeadLetterJob(
                    tenant_id=auth.tenant_id,
                    job_type="micro_infill",
                    payload={"input": ctx, "output": out.model_dump()},
                    error=f"dedup_block similarity={max_sim:.3f}",
                )
            )
            job.status = "blocked"
            job.output = out.model_dump()
            job.error = f"similarity {max_sim:.3f}"
        else:
            db.add(
                ContentHash(
                    tenant_id=auth.tenant_id,
                    page_id=body.page_id,
                    simhash=simhash64(text_blob),
                    minhash_buckets=minhash_buckets(text_blob),
                )
            )
            job.status = "done"
            job.output = {**out.model_dump(), "_provider": provider, "_model": model_used}

        job.tokens_in = tokens_in
        job.tokens_out = tokens_out
        await append_audit(
            db,
            action="ai.micro_infill",
            payload={
                "job_id": str(job.id),
                "status": job.status,
                "sim": max_sim,
                "provider": provider,
                "model": model_used,
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
        await db.refresh(job)
        return GenerateOut(
            job_id=job.id,
            status=job.status,
            output=job.output,
            tokens_in=job.tokens_in,
            tokens_out=job.tokens_out,
            dedup_similarity=max_sim,
            blocked=blocked,
            provider=provider,
            model=model_used,
        )
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = str(exc)
        db.add(
            DeadLetterJob(
                tenant_id=auth.tenant_id,
                job_type="micro_infill",
                payload={"input": ctx},
                error=str(exc),
            )
        )
        await db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dedup/check")
async def dedup_check(
    body: DedupCheck,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> dict:
    sim = compare_texts(body.text_a, body.text_b)
    return {
        "similarity": sim,
        "action": "block" if sim > 0.85 else ("warn" if sim > 0.70 else "ok"),
    }


@router.post("/combinatorial")
async def combinatorial(
    body: MutateBody,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
) -> dict:
    sentences = slot_fill_sentences(body.seeds, body.synonyms, body.context)
    return {"count": len(sentences), "sentences": sentences[:100]}


@router.get("/dlq")
async def list_dlq(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = (
        select(DeadLetterJob)
        .where(DeadLetterJob.resolved.is_(False))
        .order_by(DeadLetterJob.created_at.desc())
    )
    if auth.role != "superadmin":
        stmt = stmt.where(DeadLetterJob.tenant_id == auth.tenant_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "job_type": r.job_type,
            "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/prompts/seed")
async def seed_prompts(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    entry = PromptEntry(
        tenant_id=auth.tenant_id,
        key="micro_infill",
        version=1,
        tier="micro",
        template=DEFAULT_MICRO_PROMPT,
        schema_json=MicroInfillOut.model_json_schema(),
    )
    db.add(entry)
    await db.commit()
    return {"ok": True, "key": "micro_infill", "version": 1}
