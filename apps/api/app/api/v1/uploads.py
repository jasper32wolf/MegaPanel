from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image

from app.api.deps import AuthContext, require_roles
from app.core.config import get_settings
from app.services.audit import append_audit
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_BYTES = 8 * 1024 * 1024


@router.post("/uploads/image")
async def upload_image(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Extension not allowed")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large")

    # Magic-bytes via Pillow re-encode (TZ 12.4)
    try:
        from io import BytesIO

        img = Image.open(BytesIO(raw))
        img = img.convert("RGB") if img.mode not in {"RGB", "RGBA"} else img
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid image") from exc

    out_dir = Path("uploads") / str(auth.tenant_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.webp"
    out_path = out_dir / name
    img.save(out_path, format="WEBP", quality=85)

    await append_audit(
        db,
        action="upload.image",
        payload={"path": str(out_path), "bytes": len(raw)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"path": str(out_path), "content_type": "image/webp"}
