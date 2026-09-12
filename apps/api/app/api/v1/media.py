from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import MediaAsset
from app.schemas.phase3 import MediaOut
from app.services.audit import append_audit
from app.services.media_normalize import save_normalized

router = APIRouter()

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 8 * 1024 * 1024


@router.post("", response_model=MediaOut, status_code=201)
async def upload_media(
    file: UploadFile = File(...),
    license: str = Form("own"),
    source: str = Form(""),
    author: str = Form(""),
    normalize: bool = Form(True),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> MediaAsset:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Extension not allowed")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    out_dir = Path("uploads") / str(auth.tenant_id) / "media"
    name = f"{uuid.uuid4().hex}.webp"
    out_path = out_dir / name
    phash = None
    normalized = False
    if normalize:
        phash = save_normalized(raw, out_path, site_salt=str(auth.tenant_id))
        normalized = True
    else:
        from app.services.media_normalize import average_hash
        from io import BytesIO
        from PIL import Image

        img = Image.open(BytesIO(raw))
        phash = average_hash(img)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if img.mode not in {"RGB", "RGBA"}:
            img = img.convert("RGB")
        img.save(out_path, format="WEBP", quality=85)

    asset = MediaAsset(
        tenant_id=auth.tenant_id,
        path=str(out_path),
        content_type="image/webp",
        source=source or None,
        license=license,
        author=author or None,
        phash=phash,
        normalized=normalized,
        tags=[],
    )
    db.add(asset)
    await append_audit(
        db,
        action="media.upload",
        payload={"path": str(out_path), "phash": phash, "license": license},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("", response_model=list[MediaOut])
async def list_media(
    auth: AuthContext = Depends(require_roles(
        "superadmin", "tenant_admin", "manager", "editor", "viewer"
    )),
    db: AsyncSession = Depends(get_db),
) -> list[MediaAsset]:
    if not auth.tenant_id and auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Tenant required")
    stmt = select(MediaAsset).order_by(MediaAsset.created_at.desc())
    if auth.role != "superadmin":
        stmt = stmt.where(MediaAsset.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())
