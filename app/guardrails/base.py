from typing import Protocol

from app.agents.context import AgentContext
from app.agents.models import AgentInput, AgentOutput


class Guardrail(Protocol):
    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None: ...

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None: ...


class GuardrailSet:
    """Runs configured validation hooks in order."""

    def __init__(self, guardrails: tuple[Guardrail, ...] = ()) -> None:
        self._guardrails = guardrails

    async def validate_input(self, agent_input: AgentInput, context: AgentContext) -> None:
        for guardrail in self._guardrails:
            await guardrail.validate_input(agent_input, context)

    async def validate_output(self, output: AgentOutput, context: AgentContext) -> None:
        for guardrail in self._guardrails:
            await guardrail.validate_output(output, context)
