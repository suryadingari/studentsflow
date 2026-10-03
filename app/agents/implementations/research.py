from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.exceptions import NonRetryableFailure
from app.agents.models import AgentInput


class ResearchAgent(BaseAgent):
    agent_name = "research"
    agent_version = "1.0.0"
    description = "Placeholder for evidence research; source integrations are deferred."
    allowed_tools = frozenset({"search", "crawl4ai"})

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> Any:
        raise NonRetryableFailure("Research is not implemented in this phase")
