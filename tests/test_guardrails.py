import asyncio

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput, AgentStatus
from app.crawling.models import CrawlConfiguration, StudentCrawlerRequest
from app.crawling.tool import MockCrawl4AITool
from app.guardrails import GuardrailSet, SecretLoggingGuardrail, SourcePolicyGuardrail
from app.agents.implementations.student_crawler import StudentCrawlerAgent


class _EchoAgent(BaseAgent):
    agent_name = "guardrail_test"
    agent_version = "1.0.0"
    description = "test agent"

    async def _execute(self, agent_input, context):
        return agent_input.payload


def test_secret_guardrail_fails_closed_and_does_not_repeat_secret():
    agent = _EchoAgent(guardrails=GuardrailSet((SecretLoggingGuardrail(),)))
    result = asyncio.run(agent.execute(
        AgentInput(payload="password=long-test-secret-value"), AgentContext()))

    assert result.status == AgentStatus.FAILED
    assert "suspected secret" in result.errors[0]
    assert "long-test-secret-value" not in result.errors[0]


def test_source_policy_guardrail_rejects_missing_allowlist():
    agent = StudentCrawlerAgent(
        crawl_tool=MockCrawl4AITool(), allowed_domains={"example.org"},
        guardrails=GuardrailSet((SourcePolicyGuardrail(),)))
    result = asyncio.run(agent.execute(
        AgentInput(payload=StudentCrawlerRequest(
            source_url="https://example.org/profile",
            permitted_domains=frozenset(),
            configuration=CrawlConfiguration(allowed_domains=frozenset(), max_pages=1))),
        AgentContext(permitted_tools=frozenset({"crawl4ai"}))))

    assert result.status == AgentStatus.FAILED
    assert "allowlisted" in result.errors[0].lower()
