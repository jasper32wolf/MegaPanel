from __future__ import annotations

from app.api.deps import AuthContext, require_roles
from app.api.v1.media import upload_media
from app.db.session import get_db
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post("/uploads/image")
async def upload_image(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await upload_media(
        file=file,
        license="own",
        source="",
        author="",
        normalize=False,
        auth=auth,
        db=db,
    )
