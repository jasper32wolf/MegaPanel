from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.db.session import engine
from app.schemas.common import HealthResponse
from fastapi import APIRouter, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.responses import JSONResponse, Response

router = APIRouter()
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
@router.get("/health/live", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0", env=settings.app_env)


async def _postgres_ready() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _redis_ready() -> None:
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.ping()
    finally:
        await redis.aclose()


@router.get("/health/ready", response_model=None)
async def readiness() -> Response:
    try:
        async with asyncio.timeout(settings.health_readiness_timeout_seconds):
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_postgres_ready())
                tasks.create_task(_redis_ready())
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable"},
        )
    return JSONResponse(content={"status": "ok"})


@router.get("/metrics")
async def metrics() -> Response:
    # Single-process scrape for phase 0
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
