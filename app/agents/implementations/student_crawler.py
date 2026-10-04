import asyncio
from collections import deque
from typing import Any
from urllib.parse import urljoin

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.exceptions import NonRetryableFailure
from app.agents.models import AgentInput
from app.core.config import settings
from app.crawling.exceptions import DisallowedCrawlDomain, InvalidCrawlURL
from app.crawling.models import (
    CrawlConfiguration,
    CrawlErrorCode,
    CrawlRequest,
    CrawlResult,
    StudentCrawlerOutput,
    StudentCrawlerRequest,
)
from app.crawling.policy import DomainPolicy, intersect_domains
from app.crawling.rate_limit import PerDomainRateLimiter
from app.crawling.tool import Crawl4AITool, RealCrawl4AITool, error_result


class StudentCrawlerAgent(BaseAgent):
    """Selects allowlisted URLs and delegates fetching to the Crawl4AI tool."""

    agent_name = "student_crawler"
    agent_version = "2.0.0"
    description = "Performs bounded, allowlist-controlled public-source crawls."
    allowed_tools = frozenset({"crawl4ai"})

    def __init__(
        self,
        crawl_tool: Crawl4AITool | None = None,
        real_tool: Crawl4AITool | None = None,
        use_real_in_live_workflows: bool = False,
        *,
        allowed_domains: frozenset[str] | set[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.crawl_tool = crawl_tool or RealCrawl4AITool()
        self.real_tool = real_tool or RealCrawl4AITool()
        self.use_real_in_live_workflows = use_real_in_live_workflows
        self.allowed_domains = frozenset(allowed_domains if allowed_domains is not None
                                         else settings.allowed_domain_set)
        self._rate_limiters: dict[float, PerDomainRateLimiter] = {}

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> StudentCrawlerOutput:
        request = agent_input.payload
        if not isinstance(request, StudentCrawlerRequest):
            raise NonRetryableFailure("StudentCrawlerAgent requires StudentCrawlerRequest input")

        config = request.configuration
        tool = (self.real_tool if self.use_real_in_live_workflows
                and context.metadata.get("demo_mode") is False else self.crawl_tool)
        allowed_domains = intersect_domains(
            self.allowed_domains, config.allowed_domains, request.permitted_domains
        )
        policy = DomainPolicy(allowed_domains)
        try:
            start_url, _ = policy.validate(request.source_url)
        except InvalidCrawlURL as error:
            result = _agent_error(request.source_url, CrawlErrorCode.INVALID_URL, str(error),
                                  tool=tool)
            return StudentCrawlerOutput(results=[result], stopped_reason="invalid URL")
        except DisallowedCrawlDomain as error:
            result = _agent_error(request.source_url, CrawlErrorCode.DOMAIN_NOT_ALLOWED, str(error),
                                  tool=tool)
            return StudentCrawlerOutput(results=[result], stopped_reason="domain not allowlisted")

        limiter = self._rate_limiters.setdefault(
            config.min_request_interval_seconds,
            PerDomainRateLimiter(config.min_request_interval_seconds),
        )
        pending: deque[tuple[str, int]] = deque([(start_url, 0)])
        seen = {start_url}
        results: list[CrawlResult] = []
        attempted = 0
        stopped_reason = None
        max_requests = min(config.max_pages, config.max_crawl_hops)

        while pending and len(results) < config.max_pages and attempted < max_requests:
            url, depth = pending.popleft()
            try:
                normalized_url, domain = policy.validate(url)
            except ValueError:
                continue
            if attempted > 0:
                try:
                    context.consume_hop()
                except Exception as error:
                    results.append(_agent_error(
                        normalized_url, CrawlErrorCode.HOP_LIMIT_EXCEEDED, str(error), domain=domain,
                        tool=tool,
                    ))
                    stopped_reason = "workflow hop limit exceeded"
                    break
            try:
                self.require_tool("crawl4ai", context)
            except Exception as error:
                from app.agents.events import AgentEventType

                await self._record(AgentEventType.TOOL_PERMISSION_DENIED, context,
                                   {"tool_name": "crawl4ai", "error": str(error)})
                results.append(_agent_error(
                    normalized_url, CrawlErrorCode.PERMISSION_DENIED, str(error), domain=domain,
                    tool=tool,
                ))
                stopped_reason = "crawl4ai tool permission denied"
                break

            await limiter.wait(domain)
            crawl_request = CrawlRequest(
                requested_url=normalized_url,
                allowed_domains=allowed_domains,
                timeout_seconds=config.request_timeout_seconds,
                max_content_chars=config.max_content_chars,
            )
            attempted += 1
            try:
                crawl_result = await tool.crawl(crawl_request)
            except (asyncio.TimeoutError, TimeoutError) as error:
                crawl_result = error_result(crawl_request, CrawlErrorCode.TIMEOUT,
                                            str(error) or "Crawl timed out",
                                            tool_version=tool.tool_version)
            except (ConnectionError, OSError) as error:
                crawl_result = error_result(crawl_request, CrawlErrorCode.CONNECTION_FAILURE, str(error),
                                            tool_version=tool.tool_version)
            except Exception as error:
                crawl_result = error_result(crawl_request, CrawlErrorCode.TOOL_FAILURE, str(error),
                                            tool_version=tool.tool_version)

            if not isinstance(crawl_result, CrawlResult):
                crawl_result = error_result(
                    crawl_request, CrawlErrorCode.MALFORMED_RESULT,
                    "Crawl tool returned a value that is not CrawlResult",
                )
            elif (crawl_result.requested_url != normalized_url
                  or crawl_result.provenance.source_url != normalized_url):
                crawl_result = error_result(
                    crawl_request, CrawlErrorCode.MALFORMED_RESULT,
                    "Crawl tool result did not preserve the requested source URL in its provenance",
                    status_code=crawl_result.status_code,
                    tool_version=crawl_result.provenance.tool_version,
                )
            elif crawl_result.final_url:
                try:
                    _, final_domain = policy.validate(crawl_result.final_url)
                    crawl_result.provenance.domain = final_domain
                except ValueError as error:
                    crawl_result = _agent_error(
                        normalized_url, CrawlErrorCode.DOMAIN_NOT_ALLOWED,
                        f"Final URL is outside the permitted domain policy: {error}", domain=domain,
                        final_url=crawl_result.final_url, tool=tool,
                    )

            crawl_result.provenance.depth = depth
            if crawl_result.raw_content and len(crawl_result.raw_content) > config.max_content_chars:
                crawl_result.raw_content = crawl_result.raw_content[:config.max_content_chars]
                crawl_result.provenance.content_truncated = True
            results.append(crawl_result)

            if not crawl_result.success and crawl_result.error and crawl_result.error.code in {
                CrawlErrorCode.RATE_LIMITED,
                CrawlErrorCode.ACCESS_RESTRICTED,
            }:
                stopped_reason = "source restricted or rate-limited; crawl stopped without retry"
                break
            if not crawl_result.success or depth >= config.max_depth:
                continue
            for link in crawl_result.discovered_links:
                absolute = urljoin(crawl_result.final_url or normalized_url, link)
                try:
                    normalized_link, _ = policy.validate(absolute)
                except ValueError:
                    continue
                if normalized_link not in seen:
                    seen.add(normalized_link)
                    pending.append((normalized_link, depth + 1))

        if pending and attempted >= max_requests and stopped_reason is None:
            stopped_reason = "configured maximum crawl pages/hops reached"
        return StudentCrawlerOutput(results=results, stopped_reason=stopped_reason)


def _agent_error(
    url: str,
    code: CrawlErrorCode,
    message: str,
    *,
    domain: str | None = None,
    final_url: str | None = None,
    tool: Crawl4AITool | None = None,
) -> CrawlResult:
    from app.crawling.models import CrawlError, CrawlMetadata

    selected_tool = tool or RealCrawl4AITool()
    return CrawlResult(
        requested_url=url,
        final_url=final_url,
        success=False,
        error=CrawlError(code=code, message=message),
        provenance=CrawlMetadata(
            source_url=url,
            final_url=final_url,
            domain=domain,
            tool_name=selected_tool.tool_name,
            tool_version=selected_tool.tool_version,
        ),
    )
