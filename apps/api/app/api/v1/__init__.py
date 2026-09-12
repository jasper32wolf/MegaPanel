from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    analytics,
    auth,
    blocks,
    bulk,
    competitors,
    compliance,
    domains,
    geo,
    health,
    keywords,
    leads,
    media,
    onboarding,
    ops,
    panel,
    publish,
    security_ops,
    sites,
    taxonomy,
    tenants,
    uploads,
    wayback,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(sites.router, prefix="/sites", tags=["sites"])
api_router.include_router(keywords.router, prefix="/keywords", tags=["keywords"])
api_router.include_router(security_ops.router, prefix="/security", tags=["security"])
api_router.include_router(uploads.router, tags=["uploads"])
api_router.include_router(taxonomy.router, prefix="/taxonomy", tags=["taxonomy"])
api_router.include_router(geo.router, prefix="/geo", tags=["geo"])
api_router.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(blocks.router, prefix="/blocks", tags=["blocks"])
api_router.include_router(competitors.router, prefix="/competitors", tags=["competitors"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(publish.router, prefix="/publish", tags=["publish"])
api_router.include_router(domains.router, prefix="/domains", tags=["domains"])
api_router.include_router(bulk.router, prefix="/bulk", tags=["bulk"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(ops.router, prefix="/ops", tags=["ops"])
api_router.include_router(panel.router, prefix="/panel", tags=["panel"])
api_router.include_router(compliance.router, prefix="/compliance", tags=["compliance"])
api_router.include_router(wayback.router, prefix="/seo", tags=["seo"])
