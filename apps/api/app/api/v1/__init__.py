from __future__ import annotations

from app.api.v1 import (
    ai_providers,
    ai_workspace,
    auth,
    blocks,
    bulk,
    domains,
    geo,
    health,
    keywords,
    leads,
    media,
    onboarding,
    panel,
    projects,
    publish,
    security_ops,
    sites,
    system,
)
from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sites.router, prefix="/sites", tags=["sites"])
api_router.include_router(keywords.router, prefix="/keywords", tags=["keywords"])
api_router.include_router(security_ops.router, prefix="/security", tags=["security"])
api_router.include_router(geo.router, prefix="/geo", tags=["geo"])
api_router.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(blocks.router, prefix="/blocks", tags=["blocks"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(publish.router, prefix="/publish", tags=["publish"])
api_router.include_router(domains.router, prefix="/domains", tags=["domains"])
api_router.include_router(bulk.router, prefix="/bulk", tags=["bulk"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(panel.router, prefix="/panel", tags=["panel"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(ai_workspace.router, prefix="/ai", tags=["ai-workspace"])
api_router.include_router(ai_providers.router, prefix="/ai/providers", tags=["ai-providers"])
