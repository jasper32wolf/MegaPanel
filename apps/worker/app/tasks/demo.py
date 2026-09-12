from __future__ import annotations

import structlog

logger = structlog.get_logger("worker")


async def healthcheck_task(ctx: dict) -> dict:
    logger.info("worker_healthcheck")
    return {"ok": True}


async def drip_promote_task(ctx: dict) -> dict:
    """Daily drip: promote 30–50 URLs noindex→indexed + IndexNow (TZ 7.1)."""
    try:
        import sys
        from pathlib import Path

        api_root = Path(__file__).resolve().parents[3] / "api"
        if str(api_root) not in sys.path:
            sys.path.insert(0, str(api_root))
        from app.db.session import SessionLocal
        from app.services.drip import promote_drip

        async with SessionLocal() as session:
            result = await promote_drip(session, limit=40)
            await session.commit()
        logger.info("drip_promote", **{k: result[k] for k in ("promoted",) if k in result})
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("drip_promote_failed", error=str(exc))
        return {"promoted": 0, "error": str(exc)}


async def dsar_process_task(
    ctx: dict,
    job_id: str,
    tenant_id: str,
    action: str,
    subject_email: str | None,
    subject_phone: str | None,
) -> dict:
    """Process queued DSAR export/delete jobs."""
    try:
        import sys
        import uuid
        from datetime import UTC, datetime
        from pathlib import Path

        from sqlalchemy import select

        api_root = Path(__file__).resolve().parents[3] / "api"
        if str(api_root) not in sys.path:
            sys.path.insert(0, str(api_root))
        from app.db.session import SessionLocal
        from app.models import DsarJob
        from app.services.dsar import process_dsar_job

        async with SessionLocal() as session:
            job = (
                await session.execute(select(DsarJob).where(DsarJob.id == uuid.UUID(job_id)))
            ).scalar_one_or_none()
            result = await process_dsar_job(
                session,
                tenant_id=uuid.UUID(tenant_id),
                action=action,
                subject_email=subject_email,
                subject_phone=subject_phone,
                job_id=uuid.UUID(job_id),
            )
            if job:
                job.status = "done"
                job.result = result
                job.finished_at = datetime.now(UTC)
            await session.commit()
        logger.info("dsar_done", job_id=job_id, action=action)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("dsar_failed", error=str(exc), job_id=job_id)
        return {"error": str(exc)}
