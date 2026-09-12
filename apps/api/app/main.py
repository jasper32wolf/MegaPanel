from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware

settings = get_settings()
logger = get_logger("api")
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(settings.log_level)
    logger.info("api_start", env=settings.app_env)
    yield
    logger.info("api_stop")


app = FastAPI(
    title="Site Panel API",
    version="0.1.0",
    description="Programmatic SEO Multi-Client Lead Hub (TZ v5.6)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(api_router)


@app.get("/")
async def root() -> dict:
    return {"service": "site-panel-api", "docs": "/docs", "health": "/api/v1/health"}


@app.get("/.well-known/security.txt", response_class=PlainTextResponse)
async def well_known_security() -> str:
    path = PROJECT_ROOT / "docs" / "security" / "security.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "Contact: security@example.com\nPreferred-Languages: ru, en\n"
