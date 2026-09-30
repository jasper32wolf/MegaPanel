from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models import AuditLog, MediaAsset
from app.schemas.phase3 import ManualAssetProvenance, MediaOut, MediaReviewDecisionIn
from app.services.audit import append_audit
from app.services.media_normalize import average_hash, decode_image, save_normalized
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 8 * 1024 * 1024


def _asset_availability(asset: MediaAsset) -> str:
    meta = getattr(asset, "meta", None) or {}
    provenance = meta.get("provenance") or {}
    stored_sha256 = str((meta.get("hashes") or {}).get("stored_sha256") or "")
    if provenance.get("kind") != "manual_upload" or provenance.get("rights_confirmed") is not True:
        return "rights_missing"
    expires_at = provenance.get("license_expires_at")
    if expires_at:
        try:
            if date.fromisoformat(str(expires_at)) < date.today():
                return "expired"
        except ValueError:
            return "rights_missing"
    if len(stored_sha256) != 64 or any(char not in "0123456789abcdef" for char in stored_sha256):
        return "rights_missing"
    return "eligible"


def _asset_out(asset: MediaAsset) -> dict:
    meta = getattr(asset, "meta", None) or {}
    return {
        "id": asset.id,
        "tenant_id": asset.tenant_id,
        "path": f"/api/v1/media/{asset.id}/file",
        "content_type": asset.content_type,
        "source": asset.source,
        "license": asset.license,
        "author": asset.author,
        "phash": asset.phash,
        "normalized": asset.normalized,
        "tags": asset.tags,
        "provenance": meta.get("provenance") or {},
        "hashes": meta.get("hashes") or {},
        "availability": _asset_availability(asset),
    }


def _asset_path(asset: MediaAsset) -> Path:
    root = Path(settings.uploads_root).resolve()
    path = Path(asset.path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Media file not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return path


MEDIA_REVIEW_DECISION_ACTION = "media.review.decision"


def _stored_sha256(asset: MediaAsset) -> str:
    stored_sha256 = str(((asset.meta or {}).get("hashes") or {}).get("stored_sha256") or "")
    if len(stored_sha256) != 64 or any(char not in "0123456789abcdef" for char in stored_sha256):
        raise HTTPException(status_code=409, detail="Media asset hash is unavailable")
    return stored_sha256


def _serialize_review_decision(entry: AuditLog) -> dict:
    payload = entry.payload or {}
    return {
        "id": entry.id,
        "decision": payload.get("decision"),
        "stored_sha256": payload.get("stored_sha256"),
        "reason": payload.get("reason"),
        "manual_replacement_guidance": payload.get("manual_replacement_guidance"),
        "evidence": payload.get("evidence"),
        "actor_id": str(entry.actor_id) if entry.actor_id else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "record_hash": entry.record_hash,
    }


async def _media_review_history(
    db: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID, stored_sha256: str
) -> list[AuditLog]:
    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.tenant_id == tenant_id,
                    AuditLog.action == MEDIA_REVIEW_DECISION_ACTION,
                    AuditLog.payload["asset_id"].astext == str(asset_id),
                    AuditLog.payload["stored_sha256"].astext == stored_sha256,
                )
                .order_by(AuditLog.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def ensure_media_review_allows_use(
    db: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID, stored_sha256: str
) -> None:
    history = await _media_review_history(
        db, tenant_id=tenant_id, asset_id=asset_id, stored_sha256=stored_sha256
    )
    if not history or (history[0].payload or {}).get("decision") != "rejected":
        return
    guidance = str((history[0].payload or {}).get("manual_replacement_guidance") or "").strip()
    message = "Media asset was rejected for this stored file; replace it manually before use"
    if guidance:
        message = f"{message}: {guidance}"
    raise ValueError(message)


@router.post("", response_model=MediaOut, status_code=201)
async def upload_media(
    file: UploadFile = File(...),
    rights_basis: str = Form("own"),
    rights_confirmed: bool = Form(False),
    source_url: str = Form(""),
    source_reference: str = Form(""),
    license_name: str = Form(""),
    license_url: str = Form(""),
    license_expires_at: str = Form(""),
    author: str = Form(""),
    normalize: bool = Form(False),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    try:
        provenance = ManualAssetProvenance(
            rights_basis=rights_basis,
            rights_confirmed=rights_confirmed,
            source_url=source_url or None,
            source_reference=source_reference or None,
            license_name=license_name or None,
            license_url=license_url or None,
            license_expires_at=license_expires_at or None,
            author=author or None,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    if normalize and provenance.rights_basis != "own":
        raise HTTPException(
            status_code=400, detail="Normalization is only available for owned media"
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Extension not allowed")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large")
    original_sha256 = hashlib.sha256(raw).hexdigest()

    out_dir = Path(settings.uploads_root) / str(auth.tenant_id) / "media"
    name = f"{uuid.uuid4().hex}.webp"
    out_path = out_dir / name
    phash = None
    normalized = False
    try:
        if normalize:
            phash = save_normalized(raw, out_path, site_salt=str(auth.tenant_id))
            normalized = True
        else:
            img = decode_image(raw)
            phash = average_hash(img)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if img.mode not in {"RGB", "RGBA"}:
                img = img.convert("RGB")
            img.save(out_path, format="WEBP", quality=85)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc

    stored_sha256 = hashlib.sha256(out_path.read_bytes()).hexdigest()
    provenance_meta = {
        **provenance.model_dump(mode="json"),
        "declared_at": datetime.now(UTC).isoformat(),
        "declared_by_user_id": str(auth.user.id),
    }
    asset = MediaAsset(
        tenant_id=auth.tenant_id,
        path=str(out_path),
        content_type="image/webp",
        source=str(provenance.source_url or provenance.source_reference or "") or None,
        license=provenance.license_name or provenance.rights_basis,
        author=provenance.author,
        phash=phash,
        normalized=normalized,
        tags=[],
        meta={
            "provenance": provenance_meta,
            "hashes": {"original_sha256": original_sha256, "stored_sha256": stored_sha256},
        },
    )
    try:
        db.add(asset)
        await db.flush()
        await append_audit(
            db,
            action="media.upload",
            payload={
                "asset_id": str(asset.id),
                "phash": phash,
                "rights_basis": provenance.rights_basis,
                "rights_confirmed": True,
                "original_sha256": original_sha256,
                "stored_sha256": stored_sha256,
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
    except Exception:
        out_path.unlink(missing_ok=True)
        await db.rollback()
        raise
    await db.refresh(asset)
    return _asset_out(asset)


@router.post("/{asset_id}/review-decisions", status_code=201)
async def create_media_review_decision(
    asset_id: uuid.UUID,
    body: MediaReviewDecisionIn,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Media not found")
    if auth.role != "superadmin" and asset.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    stored_sha256 = _stored_sha256(asset)
    entry = await append_audit(
        db,
        action=MEDIA_REVIEW_DECISION_ACTION,
        payload={
            "asset_id": str(asset.id),
            "stored_sha256": stored_sha256,
            "decision": body.decision,
            "reason": body.reason.strip() if body.reason else None,
            "manual_replacement_guidance": (
                body.manual_replacement_guidance.strip()
                if body.manual_replacement_guidance
                else None
            ),
            "evidence": body.evidence.strip() if body.evidence else None,
        },
        tenant_id=asset.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return _serialize_review_decision(entry)


@router.get("/{asset_id}/review-decisions")
async def list_media_review_decisions(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Media not found")
    if auth.role != "superadmin" and asset.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    stored_sha256 = _stored_sha256(asset)
    history = list(
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.tenant_id == asset.tenant_id,
                    AuditLog.action == MEDIA_REVIEW_DECISION_ACTION,
                    AuditLog.payload["asset_id"].astext == str(asset.id),
                )
                .order_by(AuditLog.id.desc())
            )
        )
        .scalars()
        .all()
    )
    current = next(
        (entry for entry in history if (entry.payload or {}).get("stored_sha256") == stored_sha256),
        None,
    )
    return {
        "asset_id": str(asset.id),
        "stored_sha256": stored_sha256,
        "current": _serialize_review_decision(current) if current else None,
        "items": [_serialize_review_decision(entry) for entry in history],
    }


@router.get("/{asset_id}/file")
async def get_media_file(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    asset = (
        await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Media not found")
    if auth.role != "superadmin" and asset.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return FileResponse(_asset_path(asset), media_type=asset.content_type)


@router.get("", response_model=list[MediaOut])
async def list_media(
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(MediaAsset).order_by(MediaAsset.created_at.desc())
    if auth.role != "superadmin":
        stmt = stmt.where(MediaAsset.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return [_asset_out(asset) for asset in result.scalars().all()]
