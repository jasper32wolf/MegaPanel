from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.security import ACCESS_COOKIE_NAME, decode_token
from app.db.session import get_db
from app.models import User
from app.services.token_blacklist import is_blacklisted
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user: User
    tenant_id: UUID | None
    role: str
    session_id: UUID | None = None


async def require_single_operator(db: AsyncSession, user: User) -> None:
    operator_ids = list(
        (
            await db.execute(
                select(User.id).where(User.role == "superadmin", User.is_active.is_(True)).limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(operator_ids) != 1 or operator_ids[0] != user.id:
        raise HTTPException(status_code=403, detail="Single operator access required")


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    token = creds.credentials if creds else request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    jti = payload.get("jti")
    if jti and await is_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token revoked")

    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    await require_single_operator(db, user)
    tenant_raw = payload.get("tenant_id")
    tenant_id = UUID(tenant_raw) if tenant_raw else user.tenant_id
    session_raw = payload.get("sid")
    session_id = UUID(session_raw) if session_raw else None
    return AuthContext(user=user, tenant_id=tenant_id, role=user.role, session_id=session_id)


def require_roles(*roles: str):
    async def _dep(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
        if auth.role == "superadmin":
            return auth
        if auth.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return auth

    return _dep


def require_tenant(auth: AuthContext = Depends(get_current_user)) -> AuthContext:
    """Tenant IDOR Guard (CWE-639) — require tenant scope except superadmin."""
    if auth.role == "superadmin":
        return auth
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    return auth
