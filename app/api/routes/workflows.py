"""Minimal FastAPI boundary for the process-local workflow orchestrator."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.db.persistence import DatabaseConfigurationError
from app.schemas.email import ApprovalAction
from app.schemas.workflow import WorkflowRequest, WorkflowResult


router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowApprovalRequest(BaseModel):
    draft_id: str = Field(min_length=1)
    action: ApprovalAction


class WorkflowCancellationRequest(BaseModel):
    reason: str = Field(min_length=1)


def _orchestrator(request: Request):
    return request.app.state.workflow_orchestrator


@router.post("", response_model=WorkflowResult, status_code=status.HTTP_201_CREATED)
async def create_workflow(payload: WorkflowRequest, request: Request) -> WorkflowResult:
    try:
        return await _orchestrator(request).start(payload)
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.get("", response_model=list[WorkflowResult])
async def list_workflows(request: Request, limit: int = 50, offset: int = 0):
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.list_workflows(limit=limit, offset=offset)
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.get("/{workflow_id}", response_model=WorkflowResult)
async def get_workflow(workflow_id: str, request: Request) -> WorkflowResult:
    try:
        return await _orchestrator(request).get(workflow_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.get("/{workflow_id}/steps")
async def workflow_steps(workflow_id: str, request: Request):
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.list_steps(workflow_id)
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.get("/{workflow_id}/audit")
async def workflow_audit(workflow_id: str, request: Request):
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.list_audit(workflow_id)
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.get("/observability/kpis")
async def workflow_kpis(request: Request):
    repository = _orchestrator(request).persistence
    if repository is None:
        raise HTTPException(status_code=503, detail="Persistent workflow storage is not configured")
    try:
        return await repository.collect_kpis()
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.post("/{workflow_id}/approvals", response_model=WorkflowResult)
async def approve_workflow(workflow_id: str, payload: WorkflowApprovalRequest,
                           request: Request) -> WorkflowResult:
    try:
        return await _orchestrator(request).resume_after_approval(
            workflow_id, payload.draft_id, payload.action)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error


@router.post("/{workflow_id}/cancel", response_model=WorkflowResult)
async def cancel_workflow(workflow_id: str, payload: WorkflowCancellationRequest,
                          request: Request) -> WorkflowResult:
    try:
        return await _orchestrator(request).cancel(workflow_id, payload.reason)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except DatabaseConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="PostgreSQL operation failed; verify database availability and configuration.") from error
