from app.agents.base import BaseAgent
from app.agents.exceptions import DuplicateAgentError


class AgentRegistry:
    """Registers at most one implementation/version per agent identity."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        existing = self._agents.get(agent.agent_name)
        if existing is not None:
            raise DuplicateAgentError(
                f"Agent identity {agent.agent_name!r} is already registered "
                f"at version {existing.agent_version}; cannot register {agent.agent_version}"
            )
        self._agents[agent.agent_name] = agent

    def get(self, agent_name: str) -> BaseAgent:
        return self._agents[agent_name]

    def get_version(self, agent_name: str) -> str:
        return self.get(agent_name).agent_version

    def list_agents(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((name, agent.agent_version) for name, agent in self._agents.items()))
