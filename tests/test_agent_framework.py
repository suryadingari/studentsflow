import asyncio

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.events import AgentEventType, InMemoryEventRecorder
from app.agents.exceptions import (
    DuplicateAgentError,
    NonRetryableFailure,
    RetryableFailure,
    ToolPermissionDenied,
)
from app.agents.implementations import (
    DeduplicationAgent,
    EmailAgent,
    EnrichmentAgent,
    ExtractionAgent,
    FollowUpAgent,
    MatchingAgent,
    OutreachAgent,
    ResearchAgent,
    SourceDiscoveryAgent,
    StudentCrawlerAgent,
    ValidationAgent,
)
from app.agents.models import AgentInput, AgentStatus
from app.agents.registry import AgentRegistry
from app.agents.retry import RetryPolicy
from app.crawling.models import (
    CrawlConfiguration,
    SourceDiscoveryRequest,
    StudentCrawlerRequest,
)
from app.crawling.tool import MockCrawl4AITool


def run(coro):
    return asyncio.run(coro)


class EchoAgent(BaseAgent):
    agent_name = "echo"
    agent_version = "2.1.0"
    description = "Test-only echo agent"
    allowed_tools = frozenset({"search"})

    async def _execute(self, agent_input, context):
        return agent_input.payload


class ToolUsingAgent(EchoAgent):
    agent_name = "tool_user"

    async def _execute(self, agent_input, context):
        self.require_tool("search", context)
        return agent_input.payload


class FlakyAgent(EchoAgent):
    agent_name = "flaky"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0

    async def _execute(self, agent_input, context):
        self.calls += 1
        if self.calls == 1:
            raise RetryableFailure("temporary failure")
        return agent_input.payload


class BrokenAgent(EchoAgent):
    agent_name = "broken"

    async def _execute(self, agent_input, context):
        raise NonRetryableFailure("permanent failure")


def test_registry_register_get_list_and_version() -> None:
    registry = AgentRegistry()
    agent = EchoAgent()
    registry.register(agent)

    assert registry.get("echo") is agent
    assert registry.get_version("echo") == "2.1.0"
    assert registry.list_agents() == (("echo", "2.1.0"),)


def test_registry_rejects_conflicting_version_for_same_identity() -> None:
    class AnotherEcho(EchoAgent):
        agent_version = "3.0.0"

    registry = AgentRegistry()
    registry.register(EchoAgent())

    try:
        registry.register(AnotherEcho())
    except DuplicateAgentError as error:
        assert "already registered" in str(error)
    else:
        raise AssertionError("conflicting identity/version was accepted")


def test_context_defaults_and_hop_limit() -> None:
    context = AgentContext(workflow_id="flow-1", max_hops=2)

    assert context.workflow_id == "flow-1"
    assert context.execution_id
    assert context.idempotency_key

    assert context.consume_hop() == 1
    assert context.consume_hop() == 2


def test_tool_permission_requires_agent_and_context_grants() -> None:
    agent = ToolUsingAgent()

    permitted = AgentContext(
        permitted_tools=frozenset({"search"})
    )

    result = run(
        agent.execute(
            AgentInput[str](payload="query"),
            permitted,
        )
    )

    assert result.success

    denied = AgentContext(
        permitted_tools=frozenset()
    )

    result = run(
        agent.execute(
            AgentInput[str](payload="query"),
            denied,
        )
    )

    assert result.status == AgentStatus.FAILED
    assert "not permitted" in result.errors[0]


def test_agent_cannot_use_tool_not_in_its_own_allow_list() -> None:
    agent = EchoAgent()

    context = AgentContext(
        permitted_tools=frozenset({"email_provider"})
    )

    async def operation():
        agent.require_tool("email_provider", context)

    try:
        run(operation())
    except ToolPermissionDenied:
        pass
    else:
        raise AssertionError(
            "agent-level tool restriction was ignored"
        )


def test_hop_limit_success_and_explicit_failure() -> None:
    allowed = run(
        EchoAgent().execute(
            AgentInput[str](payload="ok"),
            AgentContext(
                current_hop=0,
                max_hops=1,
            ),
        )
    )

    blocked = run(
        EchoAgent().execute(
            AgentInput[str](payload="blocked"),
            AgentContext(
                current_hop=1,
                max_hops=1,
            ),
        )
    )

    assert allowed.success
    assert allowed.execution.hop_count == 1

    assert blocked.status == AgentStatus.FAILED
    assert "Hop limit exceeded" in blocked.errors[0]


def test_idempotency_duplicate_is_detected() -> None:
    agent = EchoAgent()

    first_context = AgentContext(
        idempotency_key="same-operation"
    )

    second_context = AgentContext(
        idempotency_key="same-operation"
    )

    first = run(
        agent.execute(
            AgentInput[str](payload="value"),
            first_context,
        )
    )

    duplicate = run(
        agent.execute(
            AgentInput[str](payload="value"),
            second_context,
        )
    )

    assert first.success
    assert duplicate.status == AgentStatus.FAILED
    assert "Idempotency key already used" in duplicate.errors[0]


def test_retry_policy_retries_only_retryable_failure() -> None:
    agent = FlakyAgent(
        retry_policy=RetryPolicy(max_retries=1)
    )

    recorder = InMemoryEventRecorder()
    agent.event_recorder = recorder

    result = run(
        agent.execute(
            AgentInput[str](payload="recovered"),
            AgentContext(),
        )
    )

    assert result.success
    assert result.result == "recovered"
    assert result.execution.retry_count == 1
    assert agent.calls == 2

    assert any(
        event.event_type == AgentEventType.AGENT_RETRY
        for event in recorder.events
    )


def test_nonretryable_failure_fails_without_retry() -> None:
    agent = BrokenAgent(
        retry_policy=RetryPolicy(max_retries=3)
    )

    result = run(
        agent.execute(
            AgentInput[str](payload="input"),
            AgentContext(),
        )
    )

    assert result.status == AgentStatus.FAILED
    assert result.execution.retry_count == 0
    assert "permanent failure" in result.errors[0]


def test_execution_lifecycle_records_version_times_and_events() -> None:
    recorder = InMemoryEventRecorder()

    agent = EchoAgent(
        event_recorder=recorder
    )

    result = run(
        agent.execute(
            AgentInput[str](payload="structured"),
            AgentContext(
                workflow_id="wf",
                workflow_step_id="step",
            ),
        )
    )

    assert result.success
    assert result.result == "structured"

    assert result.agent_name == "echo"
    assert result.agent_version == "2.1.0"

    assert result.execution.workflow_id == "wf"
    assert result.execution.workflow_step_id == "step"

    assert result.execution.start_time is not None
    assert result.execution.end_time is not None
    assert result.execution.duration_seconds is not None

    assert [
        event.event_type
        for event in recorder.events
    ] == [
        AgentEventType.AGENT_STARTED,
        AgentEventType.AGENT_COMPLETED,
    ]


def test_event_has_execution_and_agent_identity() -> None:
    recorder = InMemoryEventRecorder()

    context = AgentContext(
        execution_id="exec-7",
        workflow_id="wf-2",
    )

    run(
        EchoAgent(
            event_recorder=recorder
        ).execute(
            AgentInput[str](payload="x"),
            context,
        )
    )

    event = recorder.events[0]

    assert event.event_type == AgentEventType.AGENT_STARTED
    assert event.execution_id == "exec-7"
    assert event.workflow_id == "wf-2"
    assert event.agent_name == "echo"
    assert event.agent_version == "2.1.0"


def test_research_agent_requires_research_request_input() -> None:
    """
    ResearchAgent is no longer a placeholder.

    It requires a structured ResearchRequest payload.
    Passing an arbitrary string should therefore fail with
    an input-contract error rather than a "not implemented" error.
    """
    agent = ResearchAgent()

    assert agent.agent_name == "research"
    assert agent.agent_version
    assert agent.description
    assert isinstance(
        agent.allowed_tools,
        frozenset,
    )

    result = run(
        agent.execute(
            AgentInput[str](payload="placeholder"),
            AgentContext(),
        )
    )

    assert result.status == AgentStatus.FAILED
    assert result.errors
    assert "ResearchRequest" in result.errors[0]


def test_source_discovery_and_student_crawler_are_no_longer_placeholders() -> None:
    source_agent = SourceDiscoveryAgent(
        allowed_domains=frozenset({"example.org"})
    )

    source_result = run(
        source_agent.execute(
            AgentInput[SourceDiscoveryRequest](
                payload=SourceDiscoveryRequest(
                    requirement="public technical sources",
                    permitted_domains=frozenset(
                        {"example.org"}
                    ),
                )
            ),
            AgentContext(),
        )
    )

    assert source_result.success
    assert source_result.result.candidates == []

    crawler = StudentCrawlerAgent(
        MockCrawl4AITool(),
        allowed_domains={"example.org"},
    )

    crawl_result = run(
        crawler.execute(
            AgentInput[StudentCrawlerRequest](
                payload=StudentCrawlerRequest(
                    source_url="https://example.org/projects",
                    permitted_domains=frozenset(
                        {"example.org"}
                    ),
                    configuration=CrawlConfiguration(
                        allowed_domains=frozenset(
                            {"example.org"}
                        ),
                        min_request_interval_seconds=0,
                    ),
                )
            ),
            AgentContext(
                permitted_tools=frozenset(
                    {"crawl4ai"}
                )
            ),
        )
    )

    assert crawl_result.success
    assert crawl_result.result.results[0].success