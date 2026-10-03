from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.exceptions import HopLimitExceeded


class AgentContext(BaseModel):
    """Generic execution-scoped data shared with an agent."""

    model_config = ConfigDict(validate_assignment=True)

    workflow_id: str | None = None
    workflow_step_id: str | None = None
    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str | None = None
    request_id: str | None = None
    current_hop: int = 0
    max_hops: int = 20
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    permitted_tools: frozenset[str] = Field(default_factory=frozenset)
    metadata: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_hops(self) -> "AgentContext":
        if self.current_hop < 0 or self.max_hops < 1:
            raise ValueError("current_hop must be non-negative and max_hops must be positive")
        return self

    def consume_hop(self) -> int:
        if self.current_hop >= self.max_hops:
            raise HopLimitExceeded(
                f"Hop limit exceeded: current_hop={self.current_hop}, max_hops={self.max_hops}"
            )
        self.current_hop += 1
        return self.current_hop
