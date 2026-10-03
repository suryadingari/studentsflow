from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentInput(BaseModel, Generic[InputT]):
    """Typed agent input; applications define payload schemas as needed."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    payload: InputT
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionMetadata(BaseModel):
    execution_id: str
    workflow_id: str | None = None
    workflow_step_id: str | None = None
    agent_name: str
    agent_version: str
    start_time: datetime = Field(default_factory=utc_now)
    end_time: datetime | None = None
    duration_seconds: float | None = None
    status: AgentStatus = AgentStatus.RUNNING
    hop_count: int
    retry_count: int = 0
    idempotency_key: str
    error: str | None = None


class AgentOutput(BaseModel, Generic[OutputT]):
    """Typed agent result with stable execution and provenance metadata."""

    success: bool
    status: AgentStatus
    result: OutputT | None = None
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    agent_name: str
    agent_version: str
    execution: ExecutionMetadata
