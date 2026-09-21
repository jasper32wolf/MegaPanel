from __future__ import annotations

from app.core.config import get_settings
from app.schemas.common import HealthResponse
from fastapi import APIRouter
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

router = APIRouter()
settings = get_settings()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0", env=settings.app_env)


@router.get("/metrics")
async def metrics() -> Response:
    # Single-process scrape for phase 0
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
