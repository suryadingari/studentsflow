from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.agents.exceptions import NonRetryableFailure
from app.core.config import settings
from app.crawling.models import (
    RejectedSource,
    SourceAccess,
    SourceCandidate,
    SourceDiscoveryOutput,
    SourceDiscoveryRequest,
    SourceOrigin,
)
from app.crawling.policy import DomainPolicy
from app.discovery.providers import GoogleCustomSearchProvider, SearchProvider


class SourceDiscoveryAgent(BaseAgent):
    """Screens supplied public/authorized source candidates for crawl eligibility.

    Search/catalog discovery is an input boundary in this phase; this agent never
    invents URLs or claims that a source contains a particular person or fact.
    """

    agent_name = "source_discovery"
    agent_version = "2.0.0"
    description = "Screens source candidates against access and domain policy."
    allowed_tools = frozenset()

    def __init__(self, *, allowed_domains: frozenset[str] | set[str] | None = None,
                 search_provider: SearchProvider | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowed_domains = frozenset(allowed_domains if allowed_domains is not None
                                         else settings.allowed_domain_set)
        self.search_provider = search_provider or GoogleCustomSearchProvider(
            settings.google_cse_api_key, settings.google_cse_search_engine_id)

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> SourceDiscoveryOutput:
        request = agent_input.payload
        if not isinstance(request, SourceDiscoveryRequest):
            raise NonRetryableFailure("SourceDiscoveryAgent requires SourceDiscoveryRequest input")
        domains = self.allowed_domains.intersection(request.permitted_domains)
        policy = DomainPolicy(domains)
        candidates = list(request.candidates)
        search_performed = False
        search_note = None
        if request.search_enabled:
            if not self.search_provider.available:
                search_note = "Search was requested but no search provider credentials are configured; user-supplied URL mode remains available."
            else:
                try:
                    results = await self.search_provider.search(
                        request.requirement, permitted_domains=domains,
                        limit=request.max_search_results)
                    candidates.extend(SourceCandidate(
                        url=str(result.url), domain=result.domain,
                        source_type="other", reason=f"Search result: {result.title}",
                        access=SourceAccess.PUBLIC, origin=SourceOrigin.SEARCH_DISCOVERED,
                        discovery_metadata={"provider": result.provider, "title": result.title,
                                            "snippet": result.snippet, "relevance": result.relevance},
                    ) for result in results)
                    search_performed = True
                except Exception as error:
                    # Search failure is explicit. Never claim discovery succeeded.
                    diagnostic = (f"; missing symbol {error.name}" if isinstance(error, NameError)
                                  and error.name else "")
                    search_note = (f"Search provider failed ({type(error).__name__}{diagnostic}); "
                                   "user-supplied URLs were still screened.")
        accepted = []
        rejected = []
        seen_urls: set[str] = set()
        for candidate in candidates:
            try:
                normalized, _ = policy.validate(candidate.url)
            except ValueError as error:
                rejected.append(RejectedSource(url=candidate.url, reason=str(error)))
                continue
            if candidate.access not in {SourceAccess.PUBLIC, SourceAccess.AUTHORIZED}:
                rejected.append(RejectedSource(
                    url=candidate.url,
                    reason=f"Source access status is {candidate.access.value}; public/authorized access is required.",
                ))
                continue
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            candidate.url = normalized
            accepted.append(candidate)
        note = "Source candidates do not establish facts about any person."
        if search_note:
            note = f"{note} {search_note}"
        elif request.search_enabled and not search_performed:
            note = f"{note} Search was requested but returned no configured results."
        return SourceDiscoveryOutput(candidates=accepted, rejected=rejected, note=note,
                                     search_performed=search_performed,
                                     search_provider=self.search_provider.provider_name
                                     if search_performed else None)
