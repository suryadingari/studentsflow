"""Explicitly safe local demo wiring for the existing workflow agents."""

from collections.abc import Mapping

from app.agents.events import EventRecorder
from app.agents.registry import AgentRegistry
from app.agents.implementations.deduplication import DeduplicationAgent
from app.agents.implementations.email import EmailApprovalService, EmailAgent
from app.agents.implementations.enrichment import EnrichmentAgent
from app.agents.implementations.extraction import ExtractionAgent
from app.agents.implementations.follow_up import FollowUpAgent
from app.agents.implementations.matching import MatchingAgent
from app.agents.implementations.outreach import OutreachAgent
from app.agents.implementations.source_discovery import SourceDiscoveryAgent
from app.agents.implementations.student_crawler import StudentCrawlerAgent
from app.agents.implementations.validation import ValidationAgent
from app.crawling.models import CrawlResult
from app.crawling.tool import MockCrawl4AITool
from app.core.config import settings
from app.outreach.provider import MockEmailProvider
from app.outreach.services import (InMemoryOptOutService, InMemoryOutreachStore,
                                   InMemoryRateLimiter)
from app.db.persistence import PostgresEventRecorder, PostgresWorkflowRepository
from app.outreach.persistence import PersistentOptOutService, PersistentOutreachStore
from app.orchestration.orchestrator import WorkflowOrchestrator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def create_demo_orchestrator(*, crawl_responses: Mapping[str, CrawlResult] | None = None,
                             event_recorder: EventRecorder | None = None,
                             allowed_domains: frozenset[str] | set[str] | None = None,
                             sessions: async_sessionmaker[AsyncSession] | None = None,
                             database_url: str | None = None) -> WorkflowOrchestrator:
    """Build the existing workflow using network-free crawl and email doubles."""
    registry = AgentRegistry()
    persistence = None
    if sessions is not None and database_url is not None:
        persistence = PostgresWorkflowRepository(sessions, database_url)
        event_recorder = event_recorder or PostgresEventRecorder(sessions, database_url)
    common = {"event_recorder": event_recorder}
    draft_agent = EmailAgent(**common)
    if sessions is not None and database_url is not None:
        outreach_store = PersistentOutreachStore(sessions, database_url)
        opt_out = PersistentOptOutService(sessions, database_url,
                                          event_recorder=event_recorder,
                                          outreach_store=outreach_store)
    else:
        outreach_store = InMemoryOutreachStore()
        opt_out = InMemoryOptOutService(event_recorder=event_recorder, outreach_store=outreach_store)
    domains = frozenset(allowed_domains if allowed_domains is not None else settings.allowed_domain_set)
    registry.register(SourceDiscoveryAgent(allowed_domains=domains, **common))
    registry.register(StudentCrawlerAgent(crawl_tool=MockCrawl4AITool(crawl_responses),
                                         allowed_domains=domains, **common))
    registry.register(ExtractionAgent(**common))
    registry.register(ValidationAgent(**common))
    registry.register(DeduplicationAgent(**common))
    registry.register(EnrichmentAgent(**common))
    registry.register(MatchingAgent(**common))
    registry.register(draft_agent)
    registry.register(OutreachAgent(provider=MockEmailProvider(), opt_out_service=opt_out,
                                    rate_limiter=InMemoryRateLimiter(),
                                    outreach_store=outreach_store, **common))
    registry.register(FollowUpAgent(outreach_store=outreach_store, opt_out_service=opt_out, **common))
    approval = EmailApprovalService(draft_agent.draft_store, event_recorder=event_recorder)
    return WorkflowOrchestrator(registry=registry, approval_service=approval,
                                event_recorder=event_recorder, persistence=persistence)
