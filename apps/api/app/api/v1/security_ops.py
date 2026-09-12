from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, require_roles
from app.db.session import get_db
from app.models import DsarJob
from app.services.audit import append_audit
from app.services.dsar import process_dsar_job
from app.services.mfa import generate_totp_secret, provisioning_uri, verify_totp

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
    return TotpSetupOut(secret=secret, otpauth_url=provisioning_uri(secret, user.email), pending=True)


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


class DsarRequest(BaseModel):
    subject_email: str | None = None
    subject_phone: str | None = None
    action: str = Field(pattern=r"^(export|delete)$")
    process_inline: bool = True


@router.post("/dsar")
async def dsar(
    body: DsarRequest,
    auth: AuthContext = Depends(require_roles("superadmin", "tenant_admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Data Subject Access Request — queue + optional inline process (TZ 12.5)."""
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant required")
    if not body.subject_email and not body.subject_phone:
        raise HTTPException(status_code=400, detail="subject_email or subject_phone required")

    job = DsarJob(
        tenant_id=auth.tenant_id,
        action=body.action,
        subject_email=body.subject_email,
        subject_phone=body.subject_phone,
        status="queued",
    )
    db.add(job)
    await db.flush()

    result: dict | None = None
    if body.process_inline:
        result = await process_dsar_job(
            db,
            tenant_id=auth.tenant_id,
            action=body.action,
            subject_email=body.subject_email,
            subject_phone=body.subject_phone,
            job_id=job.id,
        )
        from datetime import UTC, datetime

        job.status = "done"
        job.result = result
        job.finished_at = datetime.now(UTC)
    else:
        # Best-effort enqueue to ARQ worker
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            from app.core.config import get_settings

            redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
            await redis.enqueue_job(
                "dsar_process_task",
                str(job.id),
                str(auth.tenant_id),
                body.action,
                body.subject_email,
                body.subject_phone,
            )
            job.status = "queued"
        except Exception as exc:  # noqa: BLE001
            job.status = "queued_local"
            job.result = {"enqueue_error": str(exc)}

    await append_audit(
        db,
        action=f"dsar.{body.action}",
        payload={
            "job_id": str(job.id),
            "subject_email": body.subject_email,
            "subject_phone": bool(body.subject_phone),
            "inline": body.process_inline,
        },
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    return {
        "status": job.status,
        "action": body.action,
        "job_id": str(job.id),
        "result": result,
    }


@router.get("/me")
async def me(auth: AuthContext = Depends(require_roles(
    "superadmin", "tenant_admin", "manager", "editor", "client", "viewer"
))) -> dict:
    return {
        "id": str(auth.user.id),
        "email": auth.user.email,
        "role": auth.role,
        "tenant_id": str(auth.tenant_id) if auth.tenant_id else None,
        "mfa_enabled": bool(auth.user.mfa_enabled and auth.user.totp_secret),
        "mfa_pending": bool(auth.user.totp_pending),
    }
