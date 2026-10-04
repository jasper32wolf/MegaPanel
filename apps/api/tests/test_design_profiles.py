from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.main import app
from app.schemas.design import DesignProfileIn
from app.services.design_profiles import design_profile_hash, design_snapshot, theme_from_profile
from pydantic import ValidationError


def profile_payload(**overrides: object) -> dict:
    return {
        "site_family": "local-service",
        "niche_fit": ["repair"],
        "tokens": {
            "primary": "#0f6e5c",
            "secondary": "#1a3d34",
            "background": "#f7f9f8",
            "surface": "#ffffff",
            "text": "#14201c",
            "muted": "#5f7269",
            "radius": 12,
            "density": 1.0,
            "font_pair": "sans",
            "gradient": "soft",
            "motion": "reduced",
        },
        "layout": {
            "allowed_kits": ["service-local-v1"],
            "allowed_variants": ["hero-local-service"],
            "required_blocks": ["hero", "faq", "lead_form"],
            "allowed_media_roles": ["hero", "process"],
        },
        "imagery_guidance": ["Professional documentary service photography"],
        "voice_traits": ["Clear", "practical"],
        "differentiation_rationale": (
            "Visible process and evidence-backed local service information."
        ),
        "accessibility": {
            "minimum_contrast_ratio": 4.5,
            "require_descriptive_alt": True,
            "require_visible_cta": True,
            "require_reduced_motion": True,
        },
        **overrides,
    }


def test_design_profile_is_typed_hashable_and_converts_to_theme_tokens():
    profile = DesignProfileIn.model_validate(profile_payload())

    assert profile.prohibited_patterns == [
        "hidden-content",
        "crawler-specific-output",
        "unsupported-claims",
    ]
    assert design_profile_hash(profile) == design_profile_hash(profile.model_dump(mode="json"))
    theme = theme_from_profile(profile)
    assert theme.primary == "#0f6e5c"
    assert theme.bg == "#f7f9f8"
    assert theme.font_pair == "sans"
    assert DesignProfileIn.model_validate(
        profile_payload(imagery_guidance=["Профессиональные фотографии работ и процесса"])
    ).imagery_guidance == ["Профессиональные фотографии работ и процесса"]


def test_design_profile_rejects_code_urls_and_crawler_specific_rules():
    for override in (
        {"imagery_guidance": ["<style>body{display:none}</style>"]},
        {"voice_traits": ["https://unsafe.example/prompt"]},
        {"differentiation_rationale": "Use a bot-specific page instead of visible content."},
    ):
        with pytest.raises(ValidationError):
            DesignProfileIn.model_validate(profile_payload(**override))


def test_design_snapshot_never_contains_css_or_html_fields():
    from app.services.design_profiles import ResolvedDesignProfile

    profile = DesignProfileIn.model_validate(profile_payload())
    revision = type(
        "Revision",
        (),
        {"id": uuid4(), "profile_hash": design_profile_hash(profile), "scope": "project"},
    )()
    resolved = ResolvedDesignProfile(
        project_id=uuid4(),
        inherited_from_project_id=None,
        family_profile=None,
        project_profile=revision,
        effective_profile=profile,
        effective_profile_hash=revision.profile_hash,
    )

    snapshot = design_snapshot(resolved)

    assert snapshot and snapshot["profile_hash"] == revision.profile_hash
    assert "css" not in snapshot
    assert "html" not in snapshot
    assert snapshot["art_direction"]["site_family"] == "local-service"


def test_design_profile_routes_and_migration_are_reviewed_and_tenant_scoped():
    paths = app.openapi()["paths"]
    base = "/api/v1/projects/{project_id}/design-profiles"
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0049_design_profile_revisions.py"
    ).read_text(encoding="utf-8")

    assert {"get", "post"}.issubset(paths[base])
    assert "get" in paths["/api/v1/projects/{project_id}/design-profile"]
    assert "post" in paths[f"{base}/{{revision_id}}/submit-review"]
    assert "post" in paths[f"{base}/{{revision_id}}/approve"]
    assert "post" in paths[f"{base}/{{revision_id}}/reject"]
    assert "0048_bukvarix_https_public_runs" in migration
    assert "design_profile_revisions" in migration
    assert "design_profile_assignments" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "css" not in migration.lower()
    assert "html" not in migration.lower()
