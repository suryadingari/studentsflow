import asyncio

from app.agents.context import AgentContext
from app.agents.implementations.source_discovery import SourceDiscoveryAgent
from app.agents.models import AgentInput
from app.crawling.models import (SourceAccess, SourceCandidate, SourceDiscoveryRequest,
                                 SourceOrigin, SourceType)
from app.discovery.providers import SearchResult


def run(coro):
    return asyncio.run(coro)


class _Search:
    provider_name = "test-search"
    available = True

    async def search(self, query, *, permitted_domains, limit):
        assert query == "final year computer vision student"
        assert permitted_domains == frozenset({"example.org"})
        return [SearchResult(url="https://example.org/student", title="Public student page",
                             snippet="A public page", domain="example.org", relevance=1.0,
                             provider=self.provider_name)]


def test_source_discovery_labels_origins_preserves_search_provenance_and_deduplicates():
    supplied = SourceCandidate(url="https://example.org/student", domain="example.org",
                               source_type=SourceType.PUBLIC_PROFILE, reason="User supplied",
                               access=SourceAccess.PUBLIC)
    agent = SourceDiscoveryAgent(allowed_domains={"example.org"}, search_provider=_Search())
    request = SourceDiscoveryRequest(requirement="final year computer vision student",
                                     candidates=[supplied], permitted_domains={"example.org"},
                                     search_enabled=True)
    result = run(agent.execute(AgentInput(payload=request), AgentContext())).result

    assert len(result.candidates) == 1
    assert result.candidates[0].origin == SourceOrigin.USER_SUPPLIED
    assert result.candidates[0].discovery_metadata == {}
    assert result.search_performed is True
    assert result.search_provider == "test-search"


def test_search_unavailable_is_explicit_and_user_supplied_source_still_works():
    agent = SourceDiscoveryAgent(allowed_domains={"example.org"})
    candidate = SourceCandidate(url="https://example.org/student", domain="example.org",
                                source_type=SourceType.PUBLIC_PROFILE, reason="User supplied",
                                access=SourceAccess.PUBLIC)
    result = run(agent.execute(AgentInput(payload=SourceDiscoveryRequest(
        requirement="AI student", candidates=[candidate], permitted_domains={"example.org"},
        search_enabled=True)), AgentContext())).result

    assert result.search_performed is False
    assert "no search provider credentials" in result.note
    assert result.candidates[0].origin == SourceOrigin.USER_SUPPLIED


def test_live_workflow_selects_real_crawler_tool_only_when_not_demo():
    from app.agents.implementations.student_crawler import StudentCrawlerAgent
    from app.crawling.models import CrawlResult, CrawlMetadata, CrawlConfiguration, StudentCrawlerRequest
    from app.crawling.tool import MockCrawl4AITool

    url = "https://example.org/student"

    def response(tool_version):
        return CrawlResult(requested_url=url, final_url=url, success=True, raw_content="",
                           provenance=CrawlMetadata(source_url=url, final_url=url,
                                                   tool_version=tool_version, domain="example.org"))

    demo_tool = MockCrawl4AITool({url: response("demo")})
    real_adapter = MockCrawl4AITool({url: response("live-adapter-test")})
    agent = StudentCrawlerAgent(crawl_tool=demo_tool, real_tool=real_adapter,
                                use_real_in_live_workflows=True, allowed_domains={"example.org"})
    payload = StudentCrawlerRequest(source_url=url, permitted_domains={"example.org"},
                                    configuration=CrawlConfiguration(allowed_domains={"example.org"},
                                                                     min_request_interval_seconds=0))

    run(agent.execute(AgentInput(payload=payload), AgentContext(
        permitted_tools={"crawl4ai"}, metadata={"demo_mode": False})))

    assert len(real_adapter.requests) == 1
    assert demo_tool.requests == []
