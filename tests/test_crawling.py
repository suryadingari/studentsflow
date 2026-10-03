import asyncio

import pytest

from app.agents.context import AgentContext
from app.agents.events import AgentEventType, InMemoryEventRecorder
from app.agents.exceptions import ToolPermissionDenied
from app.agents.implementations.source_discovery import SourceDiscoveryAgent
from app.agents.implementations.student_crawler import StudentCrawlerAgent
from app.agents.models import AgentInput, AgentStatus
from app.crawling.models import (
    CrawlConfiguration,
    CrawlError,
    CrawlErrorCode,
    CrawlMetadata,
    CrawlRequest,
    CrawlResult,
    SourceAccess,
    SourceCandidate,
    SourceDiscoveryOutput,
    SourceDiscoveryRequest,
    SourceType,
    StudentCrawlerRequest,
)
from app.crawling.policy import DomainPolicy, intersect_domains
from app.crawling.rate_limit import PerDomainRateLimiter
from app.crawling.tool import Crawl4AITool, MockCrawl4AITool, RealCrawl4AITool, error_result


def run(coro):
    return asyncio.run(coro)


def candidate(url: str = "https://profiles.example.org/projects",
              access: SourceAccess = SourceAccess.PUBLIC) -> SourceCandidate:
    return SourceCandidate(
        url=url,
        domain="profiles.example.org",
        source_type=SourceType.PORTFOLIO,
        reason="Public technical portfolio source candidate",
        access=access,
        discovery_metadata={"origin": "test fixture", "is_demo": True},
    )


def crawler_request(**overrides) -> StudentCrawlerRequest:
    fields = {
        "source_url": "https://profiles.example.org/projects",
        "permitted_domains": frozenset({"example.org"}),
        "configuration": CrawlConfiguration(
            allowed_domains=frozenset({"example.org"}),
            max_pages=3,
            max_depth=1,
            min_request_interval_seconds=0,
            max_crawl_hops=3,
        ),
    }
    fields.update(overrides)
    return StudentCrawlerRequest(**fields)


def fake_result(url: str, *, links: list[str] | None = None,
                status_code: int = 200, content: str = "Demo page content") -> CrawlResult:
    success = status_code < 400
    return CrawlResult(
        requested_url=url,
        final_url=url,
        status_code=status_code,
        page_title="Demo page",
        raw_content=content if success else None,
        discovered_links=links or [],
        success=success,
        error=None if success else CrawlError(
            code=CrawlErrorCode.RATE_LIMITED if status_code == 429 else CrawlErrorCode.HTTP_ERROR,
            message=f"Demo HTTP {status_code}",
        ),
        provenance=CrawlMetadata(
            source_url=url, final_url=url, domain="profiles.example.org", tool_version="mock-1.0"
        ),
    )


def test_source_candidate_creation_preserves_discovery_metadata() -> None:
    item = candidate()
    assert item.domain == "profiles.example.org"
    assert item.discovery_metadata["is_demo"] is True


def test_domain_policy_allows_exact_and_subdomains() -> None:
    policy = DomainPolicy({"example.org"})
    assert policy.validate("https://example.org/a")[1] == "example.org"
    assert policy.validate("https://profiles.example.org/a")[1] == "profiles.example.org"


def test_domain_policy_rejects_disallowed_domain_and_private_ip() -> None:
    policy = DomainPolicy({"example.org"})
    with pytest.raises(ValueError, match="not allowlisted"):
        policy.validate("https://other.org/path")
    with pytest.raises(ValueError, match="Non-public IP"):
        DomainPolicy({"127.0.0.1"}).validate("http://127.0.0.1/admin")
    with pytest.raises(ValueError, match="Local or private"):
        DomainPolicy({"localhost"}).validate("http://localhost/admin")


def test_domain_policy_rejects_invalid_urls() -> None:
    policy = DomainPolicy({"example.org"})
    for url in ("", "ftp://example.org/file", "//example.org/path", "https://user:pass@example.org"):
        with pytest.raises(ValueError):
            policy.validate(url)


def test_allowlist_intersection_respects_parent_and_narrower_domain() -> None:
    assert intersect_domains({"example.org"}, {"profiles.example.org"}) == {"profiles.example.org"}


def test_crawl_request_validates_domain_and_limits() -> None:
    request = CrawlRequest(
        requested_url="https://example.org/page",
        allowed_domains=frozenset({"example.org"}),
        timeout_seconds=5,
        max_content_chars=100,
    )
    assert request.requested_url.endswith("page")
    with pytest.raises(ValueError):
        CrawlRequest(requested_url="https://other.org/", allowed_domains=frozenset({"example.org"}))
    with pytest.raises(ValueError):
        CrawlConfiguration(max_pages=0)


def test_mock_crawl4ai_success_is_deterministic_demo_and_preserves_provenance() -> None:
    tool = MockCrawl4AITool()
    request = CrawlRequest(requested_url="https://example.org/", allowed_domains=frozenset({"example.org"}))
    first = run(tool.crawl(request))
    second = run(tool.crawl(request))
    assert first.success and second.success and first.status_code == 200
    assert first.raw_content == second.raw_content
    assert first.requested_url == second.requested_url
    assert first.provenance.source_url == request.requested_url
    assert first.provenance.final_url == request.requested_url
    assert first.provenance.tool_name == "crawl4ai"
    assert "DEMO DATA" in first.raw_content
    assert "real student profile" in first.raw_content


def test_mock_crawl4ai_failure_is_structured() -> None:
    url = "https://example.org/restricted"
    failure = error_result(
        CrawlRequest(requested_url=url, allowed_domains=frozenset({"example.org"})),
        CrawlErrorCode.ACCESS_RESTRICTED,
        "Access denied by source",
        status_code=403,
        tool_version="mock-1.0",
    )
    result = run(MockCrawl4AITool({url: failure}).crawl(
        CrawlRequest(requested_url=url, allowed_domains=frozenset({"example.org"}))
    ))
    assert not result.success
    assert result.error.code == CrawlErrorCode.ACCESS_RESTRICTED
    assert result.status_code == 403


def test_student_crawler_agent_invokes_tool_and_keeps_raw_provenance() -> None:
    url = "https://profiles.example.org/projects"
    tool = MockCrawl4AITool({url: fake_result(url, content="DEMO raw HTML/text")})
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"})
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request()),
        AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    crawl = result.result.results[0]
    assert result.success and crawl.success
    assert len(tool.requests) == 1
    assert crawl.requested_url == url
    assert crawl.provenance.source_url == url
    assert crawl.provenance.domain == "profiles.example.org"
    assert crawl.raw_content == "DEMO raw HTML/text"


def test_student_crawler_rejects_disallowed_domain_before_tool_call() -> None:
    tool = MockCrawl4AITool()
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"})
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request(
            source_url="https://private.example.net/page",
            permitted_domains=frozenset({"example.net"}),
            configuration=CrawlConfiguration(allowed_domains=frozenset({"example.net"}),
                                             min_request_interval_seconds=0),
        )), AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    assert result.result.results[0].error.code == CrawlErrorCode.DOMAIN_NOT_ALLOWED
    assert not tool.requests


def test_student_crawler_returns_structured_invalid_url_error() -> None:
    tool = MockCrawl4AITool()
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"})
    invalid_request = StudentCrawlerRequest.model_construct(
        source_url="javascript:alert(1)",
        permitted_domains=frozenset({"example.org"}),
        configuration=CrawlConfiguration(allowed_domains=frozenset({"example.org"}),
                                         min_request_interval_seconds=0),
    )
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=invalid_request),
        AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    assert result.status == AgentStatus.FAILED
    assert result.result.results[0].error.code == CrawlErrorCode.INVALID_URL
    assert not tool.requests


def test_student_crawler_tool_permission_denied_without_invocation() -> None:
    tool = MockCrawl4AITool()
    recorder = InMemoryEventRecorder()
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"}, event_recorder=recorder)
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request()), AgentContext()
    ))
    assert result.status == AgentStatus.FAILED
    assert result.result.results[0].error.code == CrawlErrorCode.PERMISSION_DENIED
    assert not tool.requests
    assert any(event.event_type == AgentEventType.TOOL_PERMISSION_DENIED for event in recorder.events)


def test_student_crawler_enforces_workflow_hop_limit_for_additional_pages() -> None:
    start = "https://profiles.example.org/projects"
    next_url = "https://profiles.example.org/projects/demo"
    tool = MockCrawl4AITool({start: fake_result(start, links=[next_url]),
                             next_url: fake_result(next_url)})
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"})
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request()),
        AgentContext(max_hops=1, permitted_tools=frozenset({"crawl4ai"})),
    ))
    assert len(tool.requests) == 1
    assert result.result.results[-1].error.code == CrawlErrorCode.HOP_LIMIT_EXCEEDED
    assert result.result.stopped_reason == "workflow hop limit exceeded"


def test_student_crawler_respects_page_and_depth_bounds() -> None:
    start = "https://profiles.example.org/projects"
    child = "https://profiles.example.org/projects/demo"

    page_tool = MockCrawl4AITool({start: fake_result(start, links=[child])})
    page_agent = StudentCrawlerAgent(page_tool, allowed_domains={"example.org"})
    page_result = run(page_agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request(configuration=CrawlConfiguration(
            allowed_domains=frozenset({"example.org"}), max_pages=1, max_depth=3,
            max_crawl_hops=5, min_request_interval_seconds=0,
        ))), AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    assert len(page_tool.requests) == 1
    assert page_result.result.stopped_reason == "configured maximum crawl pages/hops reached"

    depth_tool = MockCrawl4AITool({start: fake_result(start, links=[child])})
    depth_agent = StudentCrawlerAgent(depth_tool, allowed_domains={"example.org"})
    depth_result = run(depth_agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request(configuration=CrawlConfiguration(
            allowed_domains=frozenset({"example.org"}), max_pages=5, max_depth=0,
            max_crawl_hops=5, min_request_interval_seconds=0,
        ))), AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    assert len(depth_tool.requests) == 1
    assert depth_result.result.pages_crawled == 1


def test_idempotency_prevents_duplicate_crawl() -> None:
    tool = MockCrawl4AITool()
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"})
    input_model = AgentInput[StudentCrawlerRequest](payload=crawler_request())
    first = run(agent.execute(input_model, AgentContext(
        idempotency_key="same-crawl", permitted_tools=frozenset({"crawl4ai"})
    )))
    duplicate = run(agent.execute(input_model, AgentContext(
        idempotency_key="same-crawl", permitted_tools=frozenset({"crawl4ai"})
    )))
    assert first.success
    assert duplicate.status == AgentStatus.FAILED
    assert len(tool.requests) == 1


def test_rate_limit_response_stops_without_retrying_or_circumventing() -> None:
    url = "https://profiles.example.org/projects"
    tool = MockCrawl4AITool({url: fake_result(url, status_code=429)})
    agent = StudentCrawlerAgent(tool, allowed_domains={"example.org"})
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request()),
        AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    crawl = result.result.results[0]
    assert crawl.error.code == CrawlErrorCode.RATE_LIMITED
    assert "without retry" in result.result.stopped_reason
    assert len(tool.requests) == 1


def test_per_domain_rate_limiter_waits_between_requests() -> None:
    now = [0.0]
    delays = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        now[0] += delay

    limiter = PerDomainRateLimiter(2.0, sleep=fake_sleep, clock=lambda: now[0])

    async def operation() -> None:
        await limiter.wait("example.org")
        await limiter.wait("example.org")
        await limiter.wait("other.org")

    run(operation())
    assert delays == [2.0]


def test_malformed_tool_result_becomes_structured_crawl_error() -> None:
    class MalformedTool:
        tool_name = "crawl4ai"
        tool_version = "test"

        async def crawl(self, request):
            return {"success": True}

    agent = StudentCrawlerAgent(MalformedTool(), allowed_domains={"example.org"})
    result = run(agent.execute(
        AgentInput[StudentCrawlerRequest](payload=crawler_request()),
        AgentContext(permitted_tools=frozenset({"crawl4ai"})),
    ))
    assert result.result.results[0].error.code == CrawlErrorCode.MALFORMED_RESULT


def test_source_discovery_outputs_only_permitted_public_or_authorized_candidates() -> None:
    public = candidate()
    private = candidate("https://profiles.example.org/private", SourceAccess.RESTRICTED)
    other = SourceCandidate(
        url="https://other.org/projects",
        domain="other.org",
        source_type=SourceType.CODE_REPOSITORY,
        reason="A code repository source",
        access=SourceAccess.PUBLIC,
    )
    agent = SourceDiscoveryAgent(allowed_domains={"example.org"})
    result = run(agent.execute(
        AgentInput[SourceDiscoveryRequest](payload=SourceDiscoveryRequest(
            requirement="technical portfolio source candidates",
            candidates=[public, private, other],
            permitted_domains=frozenset({"example.org", "other.org"}),
        )), AgentContext(),
    ))
    assert isinstance(result.result, SourceDiscoveryOutput)
    assert result.result.candidates == [public]
    assert len(result.result.rejected) == 2
    assert all("student" not in source.reason.lower() for source in result.result.candidates)


def test_crawl4ai_sdk_is_isolated_behind_tool_interface() -> None:
    assert isinstance(MockCrawl4AITool(), Crawl4AITool)
    assert isinstance(RealCrawl4AITool(), Crawl4AITool)
    assert not hasattr(MockCrawl4AITool(), "execute")
    assert not hasattr(RealCrawl4AITool(), "execute")
