from __future__ import annotations

from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.api.v1.projects import _project_or_404
from app.db.session import get_db
from app.schemas.research import BukvarixProviderStatus
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_READ = require_roles("superadmin", "tenant_admin", "manager", "editor")


@router.get("/{project_id}/semantic-sources/bukvarix/status", response_model=BukvarixProviderStatus)
async def bukvarix_status(
    project_id: UUID,
    auth: AuthContext = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> BukvarixProviderStatus:
    await _project_or_404(db, project_id, auth)
    return BukvarixProviderStatus(
        message=(
            "Букварикс отключён: документированный transport использует HTTP и API key "
            "в URL. Добавьте credential-safe HTTPS contract поставщика, прежде чем включать импорт."
        ),
        supported_modes=["domain", "compare", "multi_domain"],
    )
