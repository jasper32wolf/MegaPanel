from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import AuditLog, AuthSession
from app.services.audit import append_audit, verify_audit_chain
from app.services.mfa import generate_totp_secret, provisioning_uri, verify_totp
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
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


def _serialize_session(session: AuthSession, current_family_id: UUID | None) -> dict:
    return {
        "id": str(session.id),
        "family_id": str(session.family_id),
        "device_label": session.device_label,
        "current": session.family_id == current_family_id,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "expires_at": session.expires_at.isoformat(),
        "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
    }


def _family_representatives(
    sessions: list[AuthSession], now: datetime
) -> tuple[list[AuthSession], list[AuthSession]]:
    by_family: dict[UUID, list[AuthSession]] = {}
    for session in sessions:
        by_family.setdefault(session.family_id, []).append(session)
    active: list[AuthSession] = []
    recent: list[AuthSession] = []
    for family_sessions in by_family.values():
        ordered = sorted(
            family_sessions,
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        representative = ordered[0]
        if any(item.revoked_at is None and item.expires_at > now for item in family_sessions):
            active.append(representative)
        else:
            recent.append(representative)
    active.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    recent.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return active, recent


async def _user_sessions(db: AsyncSession, user_id: UUID) -> list[AuthSession]:
    return list(
        (
            await db.execute(
                select(AuthSession)
                .where(AuthSession.user_id == user_id)
                .order_by(AuthSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


@router.get("/sessions")
async def list_sessions(
    auth: AuthContext = Depends(
        require_roles("superadmin", "tenant_admin", "manager", "editor", "viewer")
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    now = datetime.now(UTC)
    sessions = await _user_sessions(db, auth.user.id)
    current = next((item for item in sessions if item.id == auth.session_id), None)
    current_family_id = current.family_id if current else None
    active, recent = _family_representatives(sessions, now)
    return {
        "active": [_serialize_session(session, current_family_id) for session in active],
        "recent": [_serialize_session(session, current_family_id) for session in recent[:10]],
        "history_total": len(recent),
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
    sessions = await _user_sessions(db, auth.user.id)
    current = next((item for item in sessions if item.id == auth.session_id), None)
    _, recent = _family_representatives(sessions, datetime.now(UTC))
    return {
        "items": [
            _serialize_session(session, current.family_id if current else None)
            for session in recent[safe_offset : safe_offset + safe_limit]
        ],
        "total": len(recent),
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
    sessions = await _user_sessions(db, auth.user.id)
    target = next((item for item in sessions if item.id == session_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Session not found")
    current = next((item for item in sessions if item.id == auth.session_id), None)
    if current and target.family_id == current.family_id:
        raise HTTPException(status_code=409, detail="Use logout to revoke the current session")
    active_members = [
        item for item in sessions if item.family_id == target.family_id and item.revoked_at is None
    ]
    if active_members:
        now = datetime.now(UTC)
        await db.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == auth.user.id,
                AuthSession.family_id == target.family_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        for item in active_members:
            item.revoked_at = now
        await append_audit(
            db,
            action="user.session_family_revoke",
            payload={
                "session_id": str(target.id),
                "family_id": str(target.family_id),
                "count": len(active_members),
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
    return {"id": str(target.id), "revoked": True}


@router.get("/audit")
async def list_audit_history(
    action: str | None = Query(default=None, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin", "manager", "editor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    statement = select(AuditLog)
    if auth.role != "superadmin":
        statement = statement.where(AuditLog.tenant_id == auth.tenant_id)
    if action and action.strip():
        statement = statement.where(AuditLog.action == action.strip())
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(
        (await db.execute(statement.order_by(AuditLog.id.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": entry.id,
                "action": entry.action,
                "actor_id": str(entry.actor_id) if entry.actor_id else None,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "record_hash": entry.record_hash,
            }
            for entry in rows
        ],
        "offset": offset,
        "limit": limit,
        "total": int(total or 0),
    }


@router.get("/audit/integrity")
async def audit_integrity(
    auth: AuthContext = Depends(require_roles("superadmin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    entries = list((await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all())
    invalid_ids = verify_audit_chain(entries)
    return {
        "status": "valid" if not invalid_ids else "invalid",
        "checked_records": len(entries),
        "invalid_record_ids": invalid_ids,
    }


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
