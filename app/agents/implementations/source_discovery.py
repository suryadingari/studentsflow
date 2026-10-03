from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.agents.exceptions import NonRetryableFailure
from app.core.config import settings
from app.crawling.models import (
    RejectedSource,
    SourceAccess,
    SourceDiscoveryOutput,
    SourceDiscoveryRequest,
)
from app.crawling.policy import DomainPolicy


class SourceDiscoveryAgent(BaseAgent):
    """Screens supplied public/authorized source candidates for crawl eligibility.

    Search/catalog discovery is an input boundary in this phase; this agent never
    invents URLs or claims that a source contains a particular person or fact.
    """

    agent_name = "source_discovery"
    agent_version = "2.0.0"
    description = "Screens source candidates against access and domain policy."
    allowed_tools = frozenset()

    def __init__(self, *, allowed_domains: frozenset[str] | set[str] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowed_domains = frozenset(allowed_domains if allowed_domains is not None
                                         else settings.allowed_domain_set)

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> SourceDiscoveryOutput:
        request = agent_input.payload
        if not isinstance(request, SourceDiscoveryRequest):
            raise NonRetryableFailure("SourceDiscoveryAgent requires SourceDiscoveryRequest input")
        domains = self.allowed_domains.intersection(request.permitted_domains)
        policy = DomainPolicy(domains)
        accepted = []
        rejected = []
        for candidate in request.candidates:
            try:
                policy.validate(candidate.url)
            except ValueError as error:
                rejected.append(RejectedSource(url=candidate.url, reason=str(error)))
                continue
            if candidate.access not in {SourceAccess.PUBLIC, SourceAccess.AUTHORIZED}:
                rejected.append(RejectedSource(
                    url=candidate.url,
                    reason=f"Source access status is {candidate.access.value}; public/authorized access is required.",
                ))
                continue
            accepted.append(candidate)
        return SourceDiscoveryOutput(candidates=accepted, rejected=rejected)
