from __future__ import annotations

import secrets
from datetime import UTC, datetime

from app.api.deps import AuthContext, get_current_user, require_single_operator
from app.core.config import get_settings
from app.core.rate_limit import auth_limiter, client_ip
from app.core.security import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    create_access_token,
    create_refresh_token,
    decode_token,
    sha256_hex,
    verify_password,
)
from app.db.session import get_db
from app.models import AuthSession, User
from app.schemas.common import LoginRequest
from app.services.audit import append_audit
from app.services.mfa import verify_totp
from app.services.token_blacklist import blacklist_jti
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
settings = get_settings()


def _expires_at(payload: dict) -> datetime:
    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return datetime.fromtimestamp(exp, UTC)


def _refresh_jti(payload: dict) -> str:
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return jti


def _set_session_cookies(response: Response, access: str, refresh: str) -> None:
    secure = settings.app_env.lower() == "production"
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access,
        max_age=settings.access_token_ttl_minutes * 60,
        secure=secure,
        httponly=True,
        samesite="strict",
        path="/api/v1",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        secure=secure,
        httponly=True,
        samesite="strict",
        path="/api/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        secure=secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/api/v1")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def _new_session(user: User, refresh: str) -> AuthSession:
    payload = decode_token(refresh)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return AuthSession(
        user_id=user.id,
        refresh_jti_hash=sha256_hex(_refresh_jti(payload)),
        expires_at=_expires_at(payload),
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict:
    auth_limiter.check(f"login:{client_ip(request)}")
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    await require_single_operator(db, user)
    if user.mfa_enabled and user.totp_secret:
        if not body.totp_code or not verify_totp(user.totp_secret, body.totp_code):
            raise HTTPException(status_code=401, detail="TOTP required or invalid")

    refresh = create_refresh_token(user.id)
    session = _new_session(user, refresh)
    db.add(session)
    await db.flush()
    access = create_access_token(user.id, user.tenant_id, user.role, session.id)
    await append_audit(
        db,
        action="user.login",
        payload={"email": user.email},
        tenant_id=user.tenant_id,
        actor_id=user.id,
    )
    await db.commit()
    _set_session_cookies(response, access, refresh)
    return {"ok": True, "mfa_required": False}


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict:
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh session missing")
    try:
        payload = decode_token(token)
    except Exception as exc:  # noqa: BLE001
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    if payload.get("type") != "refresh":
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid token type")

    token_hash = sha256_hex(_refresh_jti(payload))
    session = (
        await db.execute(
            select(AuthSession).where(AuthSession.refresh_jti_hash == token_hash).with_for_update()
        )
    ).scalar_one_or_none()
    if session is None or session.revoked_at is not None or session.expires_at <= datetime.now(UTC):
        if session is not None:
            await db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == session.user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            await db.commit()
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh session expired or reused")

    user = (await db.execute(select(User).where(User.id == session.user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        session.revoked_at = datetime.now(UTC)
        await db.commit()
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="User inactive")
    try:
        await require_single_operator(db, user)
    except HTTPException as exc:
        session.revoked_at = datetime.now(UTC)
        await db.commit()
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Single operator access required") from exc

    new_refresh = create_refresh_token(user.id)
    replacement = _new_session(user, new_refresh)
    db.add(replacement)
    await db.flush()
    access = create_access_token(user.id, user.tenant_id, user.role, replacement.id)
    session.revoked_at = datetime.now(UTC)
    session.replaced_by_id = replacement.id
    await db.commit()
    _set_session_cookies(response, access, new_refresh)
    return {"ok": True}


@router.post("/revoke")
async def revoke(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() or request.cookies.get(ACCESS_COOKIE_NAME, "")
    if token:
        payload = decode_token(token)
        jti = payload.get("jti")
        if isinstance(jti, str) and jti:
            await blacklist_jti(jti, ttl_seconds=settings.access_token_ttl_minutes * 60)

    refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh:
        try:
            refresh_payload = decode_token(refresh)
            refresh_hash = sha256_hex(_refresh_jti(refresh_payload))
            session = (
                await db.execute(
                    select(AuthSession).where(AuthSession.refresh_jti_hash == refresh_hash)
                )
            ).scalar_one_or_none()
            if session is not None:
                session.revoked_at = datetime.now(UTC)
        except Exception:  # noqa: BLE001
            pass

    await append_audit(
        db,
        action="user.revoke",
        payload={},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    _clear_session_cookies(response)
    return {"revoked": True}
