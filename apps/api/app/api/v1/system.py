from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.api.deps import AuthContext, get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import GitHubWorkflowRunDelivery, SystemOperation
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
from app.services.operator_alerts import create_operator_alert
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
ACTIVE_STATUSES = {"requested", "queued", "in_progress"}
TERMINAL_STATUSES = {"success", "failure", "cancelled"}
FINAL_STATUSES = TERMINAL_STATUSES | {"unknown"}
_GITHUB_SIGNATURE = re.compile(r"sha256=[a-f0-9]{64}")
_MAX_GITHUB_WEBHOOK_BYTES = 65_536


def _github_webhook_signature_valid(*, raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not secret or not signature or not _GITHUB_SIGNATURE.fullmatch(signature):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _github_webhook_payload(raw_body: bytes) -> tuple[str, str, int, str] | None:
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("action") != "completed":
        return None
    repository = payload.get("repository")
    workflow_run = payload.get("workflow_run")
    if not isinstance(repository, dict) or not isinstance(workflow_run, dict):
        return None
    repository_name = repository.get("full_name")
    run_id = workflow_run.get("id")
    workflow_path = workflow_run.get("path")
    request_id = workflow_run.get("display_title")
    if (
        not isinstance(repository_name, str)
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or not isinstance(workflow_path, str)
        or workflow_path
        not in {f".github/workflows/{DEPLOY_WORKFLOW}", f".github/workflows/{RECOVERY_WORKFLOW}"}
        or not isinstance(request_id, str)
    ):
        return None
    try:
        UUID(request_id)
    except ValueError:
        return None
    return repository_name, workflow_path.removeprefix(".github/workflows/"), run_id, request_id


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


@router.post("/github/workflow-run", status_code=status.HTTP_204_NO_CONTENT)
async def github_workflow_run_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    settings = get_settings()
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unavailable")
    content_length = request.headers.get("content-length")
    if content_length and (
        not content_length.isdigit() or int(content_length) > _MAX_GITHUB_WEBHOOK_BYTES
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Invalid request"
        )
    raw_body = await request.body()
    if len(raw_body) > _MAX_GITHUB_WEBHOOK_BYTES or not _github_webhook_signature_valid(
        raw_body=raw_body,
        signature=request.headers.get("x-hub-signature-256"),
        secret=settings.github_webhook_secret,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid request")
    if request.headers.get("x-github-event") != "workflow_run":
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    delivery_id = request.headers.get("x-github-delivery")
    if not delivery_id or len(delivery_id) > 128 or any(char.isspace() for char in delivery_id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    parsed = _github_webhook_payload(raw_body)
    if parsed is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    repository, workflow, run_id, request_id = parsed
    if repository.casefold() != settings.github_repository.casefold():
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    operation = (
        await db.execute(
            select(SystemOperation)
            .where(
                SystemOperation.request_id == request_id,
                SystemOperation.workflow == workflow,
                SystemOperation.status.in_(ACTIVE_STATUSES),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if operation is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    duplicate = await db.scalar(
        select(GitHubWorkflowRunDelivery.id).where(
            GitHubWorkflowRunDelivery.github_delivery_id == delivery_id
        )
    )
    if duplicate is not None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        run = await _github().workflow_run_by_id(
            workflow=workflow, run_id=run_id, request_id=request_id
        )
    except GitHubControlError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unavailable"
        ) from error
    if run is None or operation_status(run) not in TERMINAL_STATUSES:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    next_status = operation_status(run)
    operation.status = next_status
    operation.error_code = None
    operation.workflow_run_id = run.run_id
    operation.workflow_url = run.url
    operation.completed_at = datetime.now(UTC)
    db.add(
        GitHubWorkflowRunDelivery(
            tenant_id=operation.tenant_id,
            operation_id=operation.id,
            github_delivery_id=delivery_id,
            workflow_run_id=run.run_id,
        )
    )
    create_operator_alert(
        db,
        tenant_id=operation.tenant_id,
        category="system",
        signal_code=(
            "system-operation-failed" if next_status != "success" else "system-operation-succeeded"
        ),
        title=(
            "Системная операция завершилась с ошибкой"
            if next_status != "success"
            else "Системная операция завершена"
        ),
        body="Откройте историю системных операций для подтверждённого результата workflow.",
        subject_kind="system_operation",
        subject_key=str(operation.id),
        user_id=operation.actor_id,
    )
    await append_audit(
        db,
        action="system.operation.status",
        payload={"operation_id": str(operation.id), "status": next_status},
        tenant_id=operation.tenant_id,
        actor_id=operation.actor_id,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
        create_operator_alert(
            db,
            tenant_id=auth.tenant_id,
            category="system",
            signal_code="system-operation-requested",
            title="Запрошена системная операция",
            body=(
                "Операция deploy или recovery ожидает защищённого выполнения через GitHub Actions."
            ),
            subject_kind="system_operation",
            subject_key=str(operation.id),
            user_id=auth.user.id,
        )
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
    create_operator_alert(
        db,
        tenant_id=operation.tenant_id,
        category="system",
        signal_code="system-operation-failed",
        title="Системная операция не запущена",
        body="Проверьте защищённую историю системных операций и GitHub Actions.",
        subject_kind="system_operation",
        subject_key=str(operation.id),
        user_id=auth.user.id,
    )
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
