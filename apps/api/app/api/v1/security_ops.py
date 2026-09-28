from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import AuthSession
from app.services.audit import append_audit
from app.services.mfa import generate_totp_secret, provisioning_uri, verify_totp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_url: str
    pending: bool = True


class TotpConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.post("/totp/setup", response_model=TotpSetupOut)
async def totp_setup(
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> TotpSetupOut:
    secret = generate_totp_secret()
    user = auth.user
    # Do not enable MFA until confirm — store pending only
    user.totp_pending = secret
    await append_audit(
        db,
        action="mfa.totp_setup",
        payload={"user_id": str(user.id), "pending": True},
        tenant_id=auth.tenant_id,
        actor_id=user.id,
    )
    await db.commit()
    return TotpSetupOut(
        secret=secret, otpauth_url=provisioning_uri(secret, user.email), pending=True
    )


@router.post("/totp/confirm")
async def totp_confirm(
    body: TotpConfirm,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    pending = auth.user.totp_pending
    if not pending or not verify_totp(pending, body.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    auth.user.totp_secret = pending
    auth.user.totp_pending = None
    auth.user.mfa_enabled = True
    await append_audit(
        db,
        action="mfa.totp_confirm",
        payload={"user_id": str(auth.user.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"ok": True, "mfa_enabled": True}


@router.post("/totp/disable")
async def totp_disable(
    body: TotpConfirm,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not auth.user.totp_secret or not verify_totp(auth.user.totp_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    auth.user.totp_secret = None
    auth.user.totp_pending = None
    auth.user.mfa_enabled = False
    await append_audit(
        db,
        action="mfa.totp_disable",
        payload={"user_id": str(auth.user.id)},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {"ok": True, "mfa_enabled": False}


def _serialize_session(session: AuthSession, current_session_id: UUID | None) -> dict:
    return {
        "id": str(session.id),
        "current": session.id == current_session_id,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "expires_at": session.expires_at.isoformat(),
        "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
    }


def _active_session_filter(now: datetime):
    return and_(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)


@router.get("/sessions")
async def list_sessions(
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    now = datetime.now(UTC)
    active_filter = _active_session_filter(now)
    active = list(
        (
            await db.execute(
                select(AuthSession)
                .where(AuthSession.user_id == auth.user.id, active_filter)
                .order_by(AuthSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    recent = list(
        (
            await db.execute(
                select(AuthSession)
                .where(AuthSession.user_id == auth.user.id, ~active_filter)
                .order_by(AuthSession.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    history_total = await db.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(
            AuthSession.user_id == auth.user.id,
            ~active_filter,
        )
    )
    return {
        "active": [_serialize_session(session, auth.session_id) for session in active],
        "recent": [_serialize_session(session, auth.session_id) for session in recent],
        "history_total": int(history_total or 0),
    }


@router.get("/sessions/history")
async def list_session_history(
    offset: int = 0,
    limit: int = 50,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    safe_offset = max(offset, 0)
    safe_limit = min(max(limit, 1), 100)
    now = datetime.now(UTC)
    active_filter = _active_session_filter(now)
    query = (
        select(AuthSession)
        .where(AuthSession.user_id == auth.user.id, ~active_filter)
        .order_by(AuthSession.created_at.desc())
    )
    sessions = list((await db.execute(query.offset(safe_offset).limit(safe_limit))).scalars().all())
    total = await db.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(
            AuthSession.user_id == auth.user.id,
            ~active_filter,
        )
    )
    return {
        "items": [_serialize_session(session, auth.session_id) for session in sessions],
        "total": int(total or 0),
        "offset": safe_offset,
        "limit": safe_limit,
    }


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: UUID,
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if session_id == auth.session_id:
        raise HTTPException(status_code=409, detail="Use logout to revoke the current session")
    session = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.id == session_id, AuthSession.user_id == auth.user.id
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        await append_audit(
            db,
            action="user.session_revoke",
            payload={"session_id": str(session.id)},
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
    return {"id": str(session.id), "revoked": True}


@router.get("/me")
async def me(
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "client", "viewer")
    ),
) -> dict:
    return {
        "id": str(auth.user.id),
        "email": auth.user.email,
        "mfa_enabled": bool(auth.user.mfa_enabled and auth.user.totp_secret),
        "mfa_pending": bool(auth.user.totp_pending),
    }
