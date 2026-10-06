"""Durable execution for bounded Bukvarix HTTPS public-free runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import get_settings
from app.models import ProjectBukvarixKeywordResult, ProjectBukvarixKeywordRun, SchedulerJob
from app.services.bukvarix_https import (
    MAX_RESULTS_PER_RUN,
    BukvarixHTTPSFailure,
    fetch_public_free_keywords,
    normalize_phrase,
    output_hash,
)
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def enqueue_bukvarix_keyword_run(run_id: UUID) -> None:
    """Enqueue only the durable run ID; source phrases and results stay in PostgreSQL."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await pool.enqueue_job("bukvarix_keyword_task", str(run_id))
    finally:
        await pool.aclose()


def _scheduler_owns_run():
    return (
        select(SchedulerJob.id)
        .where(
            SchedulerJob.work_type == "bukvarix_keyword",
            SchedulerJob.source_id == ProjectBukvarixKeywordRun.id,
        )
        .exists()
    )


async def due_bukvarix_keyword_run_ids(db: AsyncSession, *, limit: int = 20) -> list[UUID]:
    """Return only legacy run IDs; scheduler jobs use their own durable outbox."""
    return list(
        (
            await db.execute(
                select(ProjectBukvarixKeywordRun.id)
                .where(ProjectBukvarixKeywordRun.status == "queued", ~_scheduler_owns_run())
                .order_by(ProjectBukvarixKeywordRun.queued_at, ProjectBukvarixKeywordRun.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def recover_stale_bukvarix_keyword_runs(db: AsyncSession) -> int:
    """Make abandoned worker attempts eligible for redelivery after the task timeout."""
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    runs = list(
        (
            await db.execute(
                select(ProjectBukvarixKeywordRun)
                .where(
                    ProjectBukvarixKeywordRun.status == "running",
                    ProjectBukvarixKeywordRun.started_at < cutoff,
                    ~_scheduler_owns_run(),
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for run in runs:
        run.status = "queued"
        run.started_at = None
        run.completed_at = None
        run.failure_code = None
    if runs:
        await db.commit()
    return len(runs)


async def run_bukvarix_keyword_run(db: AsyncSession, run_id: UUID) -> dict:
    """Fetch fixed public-free suggestions into preview rows; never imports or publishes."""
    run = (
        await db.execute(
            select(ProjectBukvarixKeywordRun)
            .where(ProjectBukvarixKeywordRun.id == run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        return {"status": "missing", "run_id": str(run_id)}
    if run.status != "queued":
        return {"status": run.status, "run_id": str(run_id), "duplicate": True}
    if run.provider_mode != "https_public_free" or not isinstance(run.seed_snapshot, list):
        run.status = "failed"
        run.failure_code = "invalid_snapshot"
        run.completed_at = datetime.now(UTC)
        await db.commit()
        return {"status": run.status, "run_id": str(run_id), "error_code": run.failure_code}

    run.status = "running"
    run.started_at = datetime.now(UTC)
    run.failure_code = None
    await db.commit()
    queries_attempted = 0
    try:
        seen: set[str] = set()
        rows: list[dict] = []
        for seed in run.seed_snapshot:
            if (
                not isinstance(seed, dict)
                or not isinstance(seed.get("phrase"), str)
                or not seed.get("project_keyword_id")
            ):
                raise BukvarixHTTPSFailure("invalid_snapshot")
            try:
                source_id = UUID(str(seed["project_keyword_id"]))
            except (TypeError, ValueError) as exc:
                raise BukvarixHTTPSFailure("invalid_snapshot") from exc
            queries_attempted += 1
            for phrase, metrics in await fetch_public_free_keywords(seed["phrase"]):
                normalized = normalize_phrase(phrase)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                rows.append(
                    {
                        "phrase": phrase,
                        "normalized": normalized,
                        "metrics": metrics,
                        "source_project_keyword_id": source_id,
                    }
                )
                if len(rows) >= MAX_RESULTS_PER_RUN:
                    break
            if len(rows) >= MAX_RESULTS_PER_RUN:
                break
        run = await db.get(ProjectBukvarixKeywordRun, run_id, populate_existing=True)
        if run is None or run.status != "running":
            return {"status": run.status if run else "missing", "run_id": str(run_id)}
        db.add_all(
            [
                ProjectBukvarixKeywordResult(
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    project_id=run.project_id,
                    **row,
                )
                for row in rows
            ]
        )
        run.status = "completed"
        run.query_count = queries_attempted
        run.result_count = len(rows)
        run.output_hash = output_hash(
            [
                {
                    "phrase": row["phrase"],
                    "metrics": row["metrics"],
                    "source_project_keyword_id": str(row["source_project_keyword_id"]),
                }
                for row in rows
            ]
        )
        run.completed_at = datetime.now(UTC)
        await db.commit()
        return {"status": run.status, "run_id": str(run_id), "result_count": len(rows)}
    except BukvarixHTTPSFailure as exc:
        code = exc.code
    except Exception:  # noqa: BLE001
        code = "provider_execution_failed"
    run = await db.get(ProjectBukvarixKeywordRun, run_id, populate_existing=True)
    if run is None or run.status != "running":
        return {"status": run.status if run else "missing", "run_id": str(run_id)}
    run.status = "failed"
    run.failure_code = code
    run.query_count = queries_attempted
    run.completed_at = datetime.now(UTC)
    await db.commit()
    return {"status": run.status, "run_id": str(run_id), "error_code": code}


__all__ = [
    "due_bukvarix_keyword_run_ids",
    "enqueue_bukvarix_keyword_run",
    "recover_stale_bukvarix_keyword_runs",
    "run_bukvarix_keyword_run",
]
