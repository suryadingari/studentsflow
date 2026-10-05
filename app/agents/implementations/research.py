"""Bounded coordinator over the existing discovery and evidence agents."""

import math
import time
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.exceptions import NonRetryableFailure
from app.agents.models import AgentInput
from app.agents.registry import AgentRegistry
from app.crawling.models import SourceDiscoveryRequest, StudentCrawlerRequest
from app.schemas.research import ResearchRequest, ResearchOutput
from app.schemas.student import ValidationRequest


class ResearchAgent(BaseAgent):
    agent_name = "research"
    agent_version = "1.1.0"
    description = "Coordinates allowlisted discovery, crawling, extraction, and evidence validation within explicit budgets."
    allowed_tools = frozenset({"search", "crawl4ai"})

    def __init__(self, registry: AgentRegistry | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.registry = registry

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> ResearchOutput:
        request = agent_input.payload
        if not isinstance(request, ResearchRequest):
            raise NonRetryableFailure("ResearchAgent requires ResearchRequest input")
        if self.registry is None:
            raise NonRetryableFailure("AgentRegistry must be provided to ResearchAgent")

        try:
            discovery_agent = self.registry.get("source_discovery")
            crawler_agent = self.registry.get("student_crawler")
            extraction_agent = self.registry.get("extraction")
            validation_agent = self.registry.get("validation")
        except KeyError as error:
            raise NonRetryableFailure("ResearchAgent requires the existing discovery, crawler, extraction, and validation agents") from error

        started = time.monotonic()
        output = ResearchOutput()
        discovery_context = self._child_context(context, "discovery")
        discovered_result = await discovery_agent.execute(
            AgentInput(payload=SourceDiscoveryRequest(
                requirement=request.requirement,
                candidates=request.candidates[:request.max_sources],
                permitted_domains=request.permitted_domains,
                search_enabled=request.search_enabled,
                max_search_results=request.max_search_results,
            )), discovery_context)
        self._carry_hops(context, discovery_context)
        output.retry_count += discovered_result.execution.retry_count
        if not discovered_result.success or discovered_result.result is None:
            output.errors.extend(_safe_errors(discovered_result.errors, "Source discovery failed"))
            output.metadata.duration_seconds = time.monotonic() - started
            return output

        output.source_discovery = discovered_result.result
        sources = discovered_result.result.candidates[:request.max_sources]
        source_batch_size = max(1, math.ceil(len(sources) / request.max_iterations))
        extracted_records = []
        research_complete = bool(sources)

        # Iterations partition the bounded source list; there is no unconstrained
        # search loop and each URL can be processed at most once per execution.
        for iteration in range(request.max_iterations):
            if time.monotonic() - started >= request.max_duration_seconds:
                research_complete = False
                break
            start_index = iteration * source_batch_size
            batch = sources[start_index:start_index + source_batch_size]
            if not batch:
                break
            output.metadata.iterations += 1
            for source in batch:
                if output.metadata.sources_tried >= request.max_sources:
                    research_complete = False
                    break
                if (time.monotonic() - started >= request.max_duration_seconds
                        or output.metadata.pages_crawled >= request.max_pages):
                    research_complete = False
                    break

                output.metadata.sources_tried += 1
                remaining_pages = request.max_pages - output.metadata.pages_crawled
                crawl_configuration = request.crawl_configuration.model_copy(update={
                    "max_pages": min(request.crawl_configuration.max_pages, remaining_pages),
                })
                crawl_context = self._child_context(context, f"crawl-{output.metadata.sources_tried}")
                crawl_output = await crawler_agent.execute(
                    AgentInput(payload=StudentCrawlerRequest(
                        source_url=source.url,
                        permitted_domains=request.permitted_domains,
                        configuration=crawl_configuration,
                    )), crawl_context)
                self._carry_hops(context, crawl_context)
                output.retry_count += crawl_output.execution.retry_count
                if not crawl_output.success or crawl_output.result is None:
                    research_complete = False
                    output.errors.extend(_safe_errors(crawl_output.errors, "Crawling failed"))
                    continue

                for crawl in crawl_output.result.results:
                    if output.metadata.pages_crawled >= request.max_pages:
                        research_complete = False
                        break
                    output.metadata.pages_crawled += 1
                    output.crawl_results.append(crawl.model_copy(update={"raw_content": None}, deep=True))
                    if not crawl.success or not crawl.raw_content:
                        research_complete = False
                        continue
                    extraction_context = self._child_context(context, f"extract-{output.metadata.pages_crawled}")
                    extraction_output = await extraction_agent.execute(
                        AgentInput(payload=crawl), extraction_context)
                    self._carry_hops(context, extraction_context)
                    output.retry_count += extraction_output.execution.retry_count
                    if not extraction_output.success or extraction_output.result is None:
                        research_complete = False
                        output.errors.extend(_safe_errors(extraction_output.errors, "Extraction failed"))
                        continue
                    extracted_records.extend(_flatten(extraction_output.result))

        # An incomplete source pass must not turn absence of evidence into a
        # definitive NOT_FOUND classification.
        output.extracted_profiles = extracted_records
        for index, extracted in enumerate(extracted_records):
            if time.monotonic() - started >= request.max_duration_seconds:
                research_complete = False
                break
            source_url = (str(extracted.source_references[0].source_url)
                          if extracted.source_references else "unknown")
            validation_context = self._child_context(context, f"validate-{index}-{source_url}")
            validated = await validation_agent.execute(
                AgentInput(payload=ValidationRequest(
                    extracted=extracted,
                    research_complete=research_complete,
                    current_year=request.current_year,
                )), validation_context)
            self._carry_hops(context, validation_context)
            output.retry_count += validated.execution.retry_count
            if validated.success and validated.result is not None:
                output.validated_candidates.append(validated.result)
                output.evidence.extend(validated.result.evidence_references)
            else:
                output.errors.extend(_safe_errors(validated.errors, "Validation failed"))

        if request.search_enabled and not output.source_discovery.search_performed:
            # A failed/unconfigured requested search leaves coverage uncertain.
            research_complete = False
        output.metadata.duration_seconds = time.monotonic() - started
        return output

    @staticmethod
    def _child_context(parent: AgentContext, suffix: str) -> AgentContext:
        return parent.model_copy(update={"idempotency_key": f"{parent.idempotency_key}:{suffix}"}, deep=True)

    @staticmethod
    def _carry_hops(parent: AgentContext, child: AgentContext) -> None:
        parent.current_hop = child.current_hop


def _flatten(extracted: Any) -> list[Any]:
    items = [extracted.model_copy(update={"additional_candidates": []}, deep=True)]
    for child in getattr(extracted, "additional_candidates", []):
        items.extend(_flatten(child))
    return items


def _safe_errors(errors: list[str], fallback: str) -> list[str]:
    # Error strings from tools are untrusted; do not place connection-like secrets
    # or credentials in persisted workflow output.
    if not errors:
        return [fallback]
    return [f"{fallback}: {error}" for error in errors]
