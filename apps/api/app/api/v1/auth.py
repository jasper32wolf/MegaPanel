from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_user
from app.core.rate_limit import auth_limiter, client_ip
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models import User
from app.schemas.common import LoginRequest, TokenResponse, UserCreate, UserOut
from app.services.audit import append_audit
from app.services.mfa import verify_totp
from app.services.token_blacklist import blacklist_jti

router = APIRouter()


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    existing = await db.execute(select(User).where(User.email == body.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role=body.role,
        tenant_id=body.tenant_id,
    )
    db.add(user)
    await append_audit(
        db,
        action="user.register",
        payload={"email": user.email, "role": user.role},
        tenant_id=user.tenant_id,
        actor_id=None,
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    auth_limiter.check(f"login:{client_ip(request)}")
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.mfa_enabled and user.totp_secret:
        if not body.totp_code or not verify_totp(user.totp_secret, body.totp_code):
            raise HTTPException(status_code=401, detail="TOTP required or invalid")

    access = create_access_token(user.id, user.tenant_id, user.role)
    refresh = create_refresh_token(user.id)
    await append_audit(
        db,
        action="user.login",
        payload={"email": user.email},
        tenant_id=user.tenant_id,
        actor_id=user.id,
    )
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh)


class RefreshBody(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshBody, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")
    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    return TokenResponse(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/revoke")
async def revoke(
    request: Request,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    payload = decode_token(token)
    jti = payload.get("jti")
    if jti:
        await blacklist_jti(jti, ttl_seconds=15 * 60)
    await append_audit(
        db,
        action="user.revoke",
        payload={"jti": jti},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"revoked": True}
