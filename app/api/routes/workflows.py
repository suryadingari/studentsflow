"""Minimal FastAPI boundary for the process-local workflow orchestrator."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import log_database_failure
from app.db.persistence import DatabaseConfigurationError
from app.schemas.email import ApprovalAction
from app.schemas.workflow import WorkflowRequest, WorkflowResult
from app.auth.security import Principal, admin_principal, current_principal


router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowApprovalRequest(BaseModel):
    draft_id: str = Field(min_length=1)
    action: ApprovalAction


class WorkflowCancellationRequest(BaseModel):
    reason: str = Field(min_length=1)


def _orchestrator(request: Request):
    return request.app.state.workflow_orchestrator


async def _require_workflow_access(request: Request, workflow_id: str, principal: Principal) -> None:
    orchestrator = _orchestrator(request)
    if orchestrator.persistence is not None:
        allowed = await orchestrator.persistence.can_access_workflow(
            workflow_id, owner_id=principal.user_id, is_admin=principal.is_admin)
    else:
        try:
            runtime = await orchestrator._get_runtime(workflow_id)
            allowed = principal.is_admin or runtime.request.owner_id == principal.user_id
        except KeyError:
            allowed = False
    if not allowed:
        # Use 404 for both absent and unauthorized resources.
        raise HTTPException(status_code=404, detail="Workflow not found.")


@router.post("", response_model=WorkflowResult, status_code=status.HTTP_201_CREATED)
async def create_workflow(payload: WorkflowRequest, request: Request,
                          principal: Principal = Depends(current_principal)) -> WorkflowResult:
    try:
        owned_payload = payload.model_copy(update={"owner_id": principal.user_id}, deep=True)
        return await _orchestrator(request).submit(owned_payload)
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.get("", response_model=list[WorkflowResult])
async def list_workflows(request: Request, limit: int = 50, offset: int = 0,
                         principal: Principal = Depends(current_principal)):
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.list_workflows(limit=limit, offset=offset,
                                               owner_id=principal.user_id,
                                               include_all=principal.is_admin)
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.get("/{workflow_id}", response_model=WorkflowResult)
async def get_workflow(workflow_id: str, request: Request,
                       principal: Principal = Depends(current_principal)) -> WorkflowResult:
    try:
        await _require_workflow_access(request, workflow_id, principal)
        return await _orchestrator(request).get(workflow_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.get("/{workflow_id}/steps")
async def workflow_steps(workflow_id: str, request: Request,
                         principal: Principal = Depends(current_principal)):
    await _require_workflow_access(request, workflow_id, principal)
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.list_steps(workflow_id)
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.get("/{workflow_id}/audit")
async def workflow_audit(workflow_id: str, request: Request,
                         principal: Principal = Depends(current_principal)):
    await _require_workflow_access(request, workflow_id, principal)
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.list_audit(workflow_id)
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.get("/observability/kpis")
async def workflow_kpis(request: Request, _admin: Principal = Depends(admin_principal)):
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.collect_kpis()
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.post("/{workflow_id}/approvals", response_model=WorkflowResult)
async def approve_workflow(workflow_id: str, payload: WorkflowApprovalRequest,
                           request: Request,
                           principal: Principal = Depends(current_principal)) -> WorkflowResult:
    try:
        await _require_workflow_access(request, workflow_id, principal)
        payload.action.actor_id = principal.username
        return await _orchestrator(request).resume_after_approval(
            workflow_id, payload.draft_id, payload.action)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error


@router.post("/{workflow_id}/cancel", response_model=WorkflowResult)
async def cancel_workflow(workflow_id: str, payload: WorkflowCancellationRequest,
                          request: Request,
                          principal: Principal = Depends(current_principal)) -> WorkflowResult:
    try:
        await _require_workflow_access(request, workflow_id, principal)
        return await _orchestrator(request).cancel(workflow_id, payload.reason)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation=f"{request.method} {request.url.path}")
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL operation failed; check server logs for a redacted diagnostic.",
        ) from error
