from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.agents.context import AgentContext
from app.agents.events import AgentEvent, AgentEventType, EventRecorder
from app.agents.exceptions import (
    DuplicateExecution,
    HopLimitExceeded,
    ToolPermissionDenied,
)
from app.agents.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from app.agents.models import AgentInput, AgentOutput, AgentStatus, ExecutionMetadata
from app.agents.permissions import require_tool_permission
from app.agents.retry import RetryPolicy
from app.guardrails.base import GuardrailSet


class BaseAgent(ABC):
    """Shared, auditable execution lifecycle for versioned agents."""

    agent_name: str
    agent_version: str
    description: str
    allowed_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        idempotency_store: IdempotencyStore | None = None,
        event_recorder: EventRecorder | None = None,
        guardrails: GuardrailSet | None = None,
    ) -> None:
        if not self.agent_name or not self.agent_version:
            raise ValueError("Agents must define agent_name and agent_version")
        self.retry_policy = retry_policy or RetryPolicy()
        self.idempotency_store = idempotency_store or InMemoryIdempotencyStore()
        self.event_recorder = event_recorder
        self.guardrails = guardrails or GuardrailSet()

    def require_tool(self, tool_name: str, context: AgentContext) -> None:
        require_tool_permission(
            tool_name, allowed_tools=self.allowed_tools, permitted_tools=context.permitted_tools
        )

    async def execute(self, agent_input: AgentInput, context: AgentContext) -> AgentOutput[Any]:
        started_at = datetime.now(timezone.utc)
        execution = ExecutionMetadata(
            execution_id=context.execution_id,
            workflow_id=context.workflow_id,
            workflow_step_id=context.workflow_step_id,
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            start_time=started_at,
            hop_count=context.current_hop,
            idempotency_key=context.idempotency_key,
        )
        await self._record(AgentEventType.AGENT_STARTED, context)

        try:
            if not await self.idempotency_store.claim(context.idempotency_key):
                raise DuplicateExecution(f"Idempotency key already used: {context.idempotency_key}")
            await self.guardrails.validate_input(agent_input, context)
            context.consume_hop()
            execution.hop_count = context.current_hop

            async def operation() -> Any:
                return await self._execute(agent_input, context)

            async def record_retry(retry_count: int, delay_seconds: float) -> None:
                execution.retry_count = retry_count
                await self._record(
                    AgentEventType.AGENT_RETRY,
                    context,
                    {"retry_count": retry_count, "delay_seconds": delay_seconds},
                )

            result, retry_count = await self.retry_policy.run(operation, on_retry=record_retry)
            execution.retry_count = max(retry_count, int(getattr(result, "retry_count", 0)))
            operation_succeeded = getattr(result, "success", True)
            execution.status = AgentStatus.SUCCEEDED if operation_succeeded else AgentStatus.FAILED
            if not operation_succeeded:
                execution.error = getattr(result, "error_summary", None) or "Agent operation returned failure"
            await self.guardrails.validate_output(AgentOutput(
                success=operation_succeeded,
                status=execution.status,
                result=result,
                errors=[] if operation_succeeded else [execution.error],
                agent_name=self.agent_name,
                agent_version=self.agent_version,
                execution=execution,
            ), context)
            await self._finish(execution)
            await self._record(
                AgentEventType.AGENT_COMPLETED if operation_succeeded else AgentEventType.AGENT_FAILED,
                context,
                {} if operation_succeeded else {"error": execution.error},
            )
            return AgentOutput(
                success=operation_succeeded,
                status=execution.status,
                result=result,
                errors=[] if operation_succeeded else [execution.error],
                metadata={"retry_count": execution.retry_count},
                agent_name=self.agent_name,
                agent_version=self.agent_version,
                execution=execution,
            )
        except Exception as error:
            execution.status = AgentStatus.FAILED
            execution.error = str(error)
            if isinstance(error, HopLimitExceeded):
                event_type = AgentEventType.HOP_LIMIT_EXCEEDED
            elif isinstance(error, DuplicateExecution):
                event_type = AgentEventType.IDEMPOTENCY_DUPLICATE
            elif isinstance(error, ToolPermissionDenied):
                event_type = AgentEventType.TOOL_PERMISSION_DENIED
            else:
                event_type = AgentEventType.AGENT_FAILED
            await self._finish(execution)
            await self._record(event_type, context, {"error": str(error)})
            return AgentOutput(
                success=False,
                status=AgentStatus.FAILED,
                errors=[str(error)],
                agent_name=self.agent_name,
                agent_version=self.agent_version,
                execution=execution,
            )

    async def _finish(self, execution: ExecutionMetadata) -> None:
        end_time = datetime.now(timezone.utc)
        execution.end_time = end_time
        execution.duration_seconds = max(0.0, (end_time - execution.start_time).total_seconds())

    async def _record(
        self,
        event_type: AgentEventType,
        context: AgentContext,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.event_recorder is not None:
            await self.event_recorder.record(AgentEvent(
                event_type=event_type,
                execution_id=context.execution_id,
                workflow_id=context.workflow_id,
                workflow_step_id=context.workflow_step_id,
                agent_name=self.agent_name,
                agent_version=self.agent_version,
                details=details or {},
            ))

    @abstractmethod
    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> Any:
        """Implement one agent-specific operation without owning lifecycle logic."""
