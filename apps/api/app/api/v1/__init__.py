from __future__ import annotations

from app.api.v1 import (
    ai_content,
    ai_providers,
    ai_workspace,
    auth,
    authors,
    blocks,
    bulk,
    competitors,
    design_profiles,
    domains,
    geo,
    health,
    index_schedules,
    intent_generation,
    keywords,
    lead_routing,
    leads,
    media,
    panel,
    project_families,
    projects,
    prompts,
    security_ops,
    semantic,
    semantic_sources,
    site_structure,
    sites,
    system,
    telemetry,
)
from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sites.router, prefix="/sites", tags=["sites"])
api_router.include_router(keywords.router, prefix="/keywords", tags=["keywords"])
api_router.include_router(security_ops.router, prefix="/security", tags=["security"])
api_router.include_router(geo.router, prefix="/geo", tags=["geo"])
api_router.include_router(blocks.router, prefix="/blocks", tags=["blocks"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(domains.router, prefix="/domains", tags=["domains"])
api_router.include_router(competitors.router, prefix="/competitors", tags=["competitors"])
api_router.include_router(bulk.router, prefix="/bulk", tags=["bulk"])
api_router.include_router(leads.router, prefix="/leads", tags=["leads"])
api_router.include_router(lead_routing.router, prefix="/projects", tags=["lead-routing"])
api_router.include_router(panel.router, prefix="/panel", tags=["panel"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(project_families.router, prefix="/projects", tags=["project-families"])
api_router.include_router(design_profiles.router, prefix="/projects", tags=["design-profiles"])
api_router.include_router(authors.router, prefix="/projects", tags=["authors"])
api_router.include_router(semantic.router, prefix="/projects", tags=["semantic"])
api_router.include_router(semantic_sources.router, prefix="/projects", tags=["semantic-sources"])
api_router.include_router(site_structure.router, prefix="/projects", tags=["site-structure"])
api_router.include_router(ai_content.router, prefix="/projects", tags=["ai-content"])
api_router.include_router(intent_generation.router, prefix="/projects", tags=["intent-generation"])
api_router.include_router(index_schedules.router, prefix="/projects", tags=["index-schedules"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(ai_workspace.router, prefix="/ai", tags=["ai-workspace"])
api_router.include_router(prompts.router, prefix="/ai/prompts", tags=["ai-prompts"])
api_router.include_router(ai_providers.router, prefix="/ai/providers", tags=["ai-providers"])
api_router.include_router(telemetry.router, prefix="/telemetry", tags=["telemetry"])
