from __future__ import annotations

import hashlib
import json
from uuid import UUID

from app.models import Site
from app.models.publish import BuildReleaseGate, SiteBuild
from app.services.site_build_metadata import validate_page_metadata_snapshot
from site_panel_shared.manifests import SiteManifest
from sqlalchemy.ext.asyncio import AsyncSession

RELEASE_GATE_RULESET_VERSION = "immutable-build-v1"


def legal_snapshot_hash(build: SiteBuild) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "build_hash": getattr(build, "build_hash", None),
                "legal": (build.manifest_snapshot or {}).get("legal") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def legal_review_status(build: SiteBuild) -> dict:
    if not hasattr(build, "legal_review"):
        return {
            "status": "pass",
            "snapshot_hash": legal_snapshot_hash(build),
            "review": {
                "state": "legacy",
                "evidence_ref": None,
                "reason": None,
                "replacement_guidance": None,
                "reviewed_at": None,
            },
            "blockers": [],
        }
    review = build.legal_review or {}
    expected_hash = legal_snapshot_hash(build)
    approved = (
        review.get("state") == "approved"
        and review.get("legal_snapshot_hash") == expected_hash
        and isinstance(review.get("evidence_ref"), str)
        and bool(review["evidence_ref"].strip())
        and review.get("reviewed_at")
    )
    return {
        "status": "pass" if approved else "block",
        "snapshot_hash": expected_hash,
        "review": {
            "state": review.get("state") or "pending",
            "evidence_ref": review.get("evidence_ref") or None,
            "reason": review.get("reason") or None,
            "replacement_guidance": review.get("replacement_guidance") or None,
            "reviewed_at": review.get("reviewed_at") or None,
        },
        "blockers": [] if approved else ["Approve the legal review for this candidate build"],
    }


def build_snapshot_hash(build: SiteBuild) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "manifest": build.manifest_snapshot,
                "page_metadata": build.page_metadata_snapshot,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def evaluate_build_release_gate(build: SiteBuild, site: Site) -> dict:
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        manifest = SiteManifest.model_validate(build.manifest_snapshot)
    except ValueError:
        manifest = None
        blockers.append("Selected build manifest snapshot is invalid")
    if manifest is not None:
        if manifest.site_id != site.id or manifest.tenant_id != site.tenant_id:
            blockers.append("Selected build manifest snapshot does not belong to this site")
        legal = manifest.legal or {}
        for key, message in {
            "org": "Set the legal organization before publish",
            "address": "Set the legal address before publish",
            "jurisdiction": "Set the legal jurisdiction before publish",
            "privacy_email": "Set a public privacy/DSAR email before publish",
        }.items():
            if not str(legal.get(key) or "").strip():
                blockers.append(message)
        try:
            metadata = validate_page_metadata_snapshot(
                build.page_metadata_snapshot,
                expected_slugs={page.slug for page in manifest.pages},
            )
        except ValueError:
            blockers.append("Selected build page metadata snapshot is invalid")
        else:
            indexed = [slug for slug, item in metadata.items() if item["index_state"] == "indexed"]
            if indexed:
                warnings.append(
                    "Indexed pages require an explicit operator promotion workflow: "
                    f"{', '.join(indexed)}"
                )
    elif build.page_metadata_snapshot is None:
        blockers.append("Selected build has no immutable page metadata snapshot")
    snapshot_hash = build_snapshot_hash(build)
    return {
        "status": "pass" if not blockers else "block",
        "ruleset_version": RELEASE_GATE_RULESET_VERSION,
        "snapshot_hash": snapshot_hash,
        "blockers": blockers,
        "warnings": warnings,
    }


async def store_release_gate(
    db: AsyncSession,
    *,
    build: SiteBuild,
    site: Site,
    actor_id: UUID | None,
) -> BuildReleaseGate:
    evaluation = evaluate_build_release_gate(build, site)
    gate = await db.get(BuildReleaseGate, build.id)
    if gate is None:
        gate = BuildReleaseGate(build_id=build.id, tenant_id=build.tenant_id)
        db.add(gate)
    gate.status = evaluation["status"]
    gate.ruleset_version = evaluation["ruleset_version"]
    gate.snapshot_hash = evaluation["snapshot_hash"]
    gate.blockers = evaluation["blockers"]
    gate.warnings = evaluation["warnings"]
    gate.evaluated_by = actor_id
    return gate


def serialize_release_gate(gate: BuildReleaseGate | None) -> dict | None:
    if gate is None:
        return None
    return {
        "status": gate.status,
        "ruleset_version": gate.ruleset_version,
        "snapshot_hash": gate.snapshot_hash,
        "blockers": gate.blockers or [],
        "warnings": gate.warnings or [],
        "evaluated_at": gate.evaluated_at.isoformat() if gate.evaluated_at else None,
    }
