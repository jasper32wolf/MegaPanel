from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.api.deps import AuthContext, get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import SystemOperation
from app.schemas.system import (
    GitHubControlOut,
    RecoveryRequest,
    SystemOperationOut,
    UpdateRequest,
    VerifiedReleasesOut,
)
from app.services.audit import append_audit
from app.services.github_control import (
    DEPLOY_WORKFLOW,
    RECOVERY_WORKFLOW,
    GitHubControl,
    GitHubControlError,
    operation_status,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
ACTIVE_STATUSES = {"requested", "queued", "in_progress"}
TERMINAL_STATUSES = {"success", "failure", "cancelled"}
FINAL_STATUSES = TERMINAL_STATUSES | {"unknown"}


def _operation_out(operation: SystemOperation) -> SystemOperationOut:
    return SystemOperationOut(
        id=operation.id,
        kind=operation.kind,
        action=operation.action,
        release_sha=operation.release_sha,
        snapshot_id=operation.snapshot_id,
        request_id=operation.request_id,
        workflow=operation.workflow,
        workflow_run_id=operation.workflow_run_id,
        workflow_url=operation.workflow_url,
        status=operation.status,
        error_code=operation.error_code,
        created_at=operation.created_at.isoformat() if operation.created_at else None,
        updated_at=operation.updated_at.isoformat() if operation.updated_at else None,
        completed_at=operation.completed_at.isoformat() if operation.completed_at else None,
    )


def _github() -> GitHubControl:
    settings = get_settings()
    return GitHubControl(
        repository=settings.github_repository,
        token=settings.github_control_token,
        api_url=settings.github_api_url,
    )


def _control_configured() -> bool:
    try:
        _github()
    except GitHubControlError:
        return False
    return True


def _github_http_error(error: GitHubControlError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code})


async def require_system_operator(
    auth: AuthContext = Depends(get_current_user),  # noqa: B008
) -> AuthContext:
    if auth.role != "superadmin":
        raise HTTPException(status_code=403, detail="Single operator access required")
    if not auth.user.mfa_enabled or not auth.user.totp_secret:
        raise HTTPException(status_code=403, detail="Confirm TOTP before system operations")
    return auth


async def _refresh_operation(
    operation: SystemOperation,
    *,
    db: AsyncSession,
    auth: AuthContext,
) -> SystemOperation:
    if operation.status not in ACTIVE_STATUSES:
        return operation
    try:
        run = await _github().workflow_run(
            workflow=operation.workflow,
            request_id=operation.request_id,
        )
    except GitHubControlError as error:
        operation.error_code = error.code
        if error.code not in {
            "github_control_not_configured",
            "github_repository_invalid",
            "github_api_url_invalid",
        }:
            await db.commit()
            return operation
        operation.status = "unknown"
        operation.completed_at = datetime.now(UTC)
        await append_audit(
            db,
            action="system.operation.status",
            payload={
                "operation_id": str(operation.id),
                "status": "unknown",
                "error_code": error.code,
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
        await db.refresh(operation)
        return operation

    next_status = operation_status(run)
    changed = next_status != operation.status
    operation.status = next_status
    operation.error_code = None
    if run:
        operation.workflow_run_id = run.run_id
        operation.workflow_url = run.url
    if next_status in FINAL_STATUSES and operation.completed_at is None:
        operation.completed_at = datetime.now(UTC)
    if changed:
        await append_audit(
            db,
            action="system.operation.status",
            payload={"operation_id": str(operation.id), "status": next_status},
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
    await db.commit()
    await db.refresh(operation)
    return operation


async def _active_operation(db: AsyncSession, tenant_id: UUID | None) -> SystemOperation | None:
    if tenant_id is None:
        return None
    return (
        await db.execute(
            select(SystemOperation)
            .where(
                SystemOperation.tenant_id == tenant_id,
                SystemOperation.status.in_(ACTIVE_STATUSES),
            )
            .order_by(SystemOperation.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _create_operation(
    *,
    kind: str,
    action: str,
    workflow: str,
    release_sha: str | None,
    snapshot_id: str | None,
    auth: AuthContext,
    db: AsyncSession,
) -> SystemOperation:
    if not auth.tenant_id:
        raise HTTPException(status_code=403, detail="Operator owner scope is missing")
    conflict = await _active_operation(db, auth.tenant_id)
    if conflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "system_operation_in_progress", "operation_id": str(conflict.id)},
        )
    operation = SystemOperation(
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
        kind=kind,
        action=action,
        release_sha=release_sha,
        snapshot_id=snapshot_id,
        request_id=str(uuid4()),
        workflow=workflow,
        status="requested",
        details={},
    )
    db.add(operation)
    try:
        await db.flush()
        await append_audit(
            db,
            action=f"system.{kind}.request",
            payload={
                "operation_id": str(operation.id),
                "action": action,
                "release_sha": release_sha,
                "snapshot_id": snapshot_id,
            },
            tenant_id=auth.tenant_id,
            actor_id=auth.user.id,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        conflict = await _active_operation(db, auth.tenant_id)
        if conflict:
            raise HTTPException(
                status_code=409,
                detail={"code": "system_operation_in_progress", "operation_id": str(conflict.id)},
            ) from None
        raise
    await db.refresh(operation)
    return operation


async def _dispatch_failure(
    operation: SystemOperation,
    *,
    error: GitHubControlError,
    auth: AuthContext,
    db: AsyncSession,
) -> None:
    operation.status = "failure"
    operation.error_code = error.code
    operation.completed_at = datetime.now(UTC)
    await append_audit(
        db,
        action="system.operation.status",
        payload={"operation_id": str(operation.id), "status": "failure", "error_code": error.code},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()


async def _mark_queued(
    operation: SystemOperation,
    *,
    auth: AuthContext,
    db: AsyncSession,
) -> SystemOperation:
    operation.status = "queued"
    await append_audit(
        db,
        action="system.operation.status",
        payload={"operation_id": str(operation.id), "status": "queued"},
        tenant_id=auth.tenant_id,
        actor_id=auth.user.id,
    )
    await db.commit()
    await db.refresh(operation)
    return operation


@router.get("/control", response_model=GitHubControlOut)
async def github_control_status(
    auth: AuthContext = Depends(require_system_operator),  # noqa: B008
) -> GitHubControlOut:
    settings = get_settings()
    configured = _control_configured()
    return GitHubControlOut(
        configured=configured,
        repository=settings.github_repository if configured else None,
        message=(
            "GitHub Actions control is available through protected Environments."
            if configured
            else "Configure GitHub control on the VPS or use GitHub Actions manually."
        ),
    )


@router.get("/updates/available", response_model=VerifiedReleasesOut)
async def available_updates(
    auth: AuthContext = Depends(require_system_operator),  # noqa: B008
) -> VerifiedReleasesOut:
    if not _control_configured():
        return VerifiedReleasesOut(configured=False)
    try:
        releases = await _github().successful_releases()
    except GitHubControlError as error:
        raise _github_http_error(error) from error
    return VerifiedReleasesOut(
        configured=True,
        releases=[
            {"sha": item.sha, "updated_at": item.updated_at, "workflow_url": item.url}
            for item in releases
        ],
    )


@router.get("/operations", response_model=list[SystemOperationOut])
async def list_operations(
    auth: AuthContext = Depends(require_system_operator),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[SystemOperationOut]:
    if not auth.tenant_id:
        return []
    operations = list(
        (
            await db.execute(
                select(SystemOperation)
                .where(SystemOperation.tenant_id == auth.tenant_id)
                .order_by(SystemOperation.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    for operation in operations:
        await _refresh_operation(operation, db=db, auth=auth)
    return [_operation_out(operation) for operation in operations]


@router.get("/operations/{operation_id}", response_model=SystemOperationOut)
async def get_operation(
    operation_id: UUID,
    auth: AuthContext = Depends(require_system_operator),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> SystemOperationOut:
    operation = (
        await db.execute(
            select(SystemOperation).where(
                SystemOperation.id == operation_id,
                SystemOperation.tenant_id == auth.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=404, detail="System operation not found")
    operation = await _refresh_operation(operation, db=db, auth=auth)
    return _operation_out(operation)


@router.post("/updates", response_model=SystemOperationOut, status_code=202)
async def request_update(
    body: UpdateRequest,
    auth: AuthContext = Depends(require_system_operator),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> SystemOperationOut:
    try:
        github = _github()
    except GitHubControlError as error:
        raise _github_http_error(error) from error
    operation = await _create_operation(
        kind="update",
        action="deploy",
        workflow=DEPLOY_WORKFLOW,
        release_sha=body.release_sha,
        snapshot_id=None,
        auth=auth,
        db=db,
    )
    try:
        await github.dispatch_deploy(release_sha=body.release_sha, request_id=operation.request_id)
    except GitHubControlError as error:
        await _dispatch_failure(operation, error=error, auth=auth, db=db)
        raise _github_http_error(error) from error
    operation = await _mark_queued(operation, auth=auth, db=db)
    return _operation_out(operation)


@router.post("/recovery", response_model=SystemOperationOut, status_code=202)
async def request_recovery(
    body: RecoveryRequest,
    auth: AuthContext = Depends(require_system_operator),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> SystemOperationOut:
    try:
        github = _github()
    except GitHubControlError as error:
        raise _github_http_error(error) from error
    operation = await _create_operation(
        kind="recovery",
        action=body.action,
        workflow=RECOVERY_WORKFLOW,
        release_sha=None,
        snapshot_id=body.snapshot_id,
        auth=auth,
        db=db,
    )
    try:
        await github.dispatch_recovery(
            action=body.action,
            snapshot=body.snapshot_id,
            request_id=operation.request_id,
        )
    except GitHubControlError as error:
        await _dispatch_failure(operation, error=error, auth=auth, db=db)
        raise _github_http_error(error) from error
    operation = await _mark_queued(operation, auth=auth, db=db)
    return _operation_out(operation)
