import importlib.metadata
import os
import time
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from app.crawling.exceptions import DisallowedCrawlDomain
from app.crawling.models import (
    CrawlError,
    CrawlErrorCode,
    CrawlMetadata,
    CrawlRequest,
    CrawlResult,
)
from app.crawling.policy import DomainPolicy


@runtime_checkable
class Crawl4AITool(Protocol):
    tool_name: str
    tool_version: str

    async def crawl(self, request: CrawlRequest) -> CrawlResult: ...


def _metadata(request: CrawlRequest, tool_version: str, started: float,
              final_url: str | None = None, domain: str | None = None,
              depth: int = 0, truncated: bool = False) -> CrawlMetadata:
    return CrawlMetadata(
        source_url=request.requested_url,
        final_url=final_url,
        domain=domain,
        tool_version=tool_version,
        duration_seconds=max(0.0, time.monotonic() - started),
        depth=depth,
        content_truncated=truncated,
    )


def error_result(
    request: CrawlRequest,
    code: CrawlErrorCode,
    message: str,
    *,
    status_code: int | None = None,
    tool_version: str = "unavailable",
    started: float | None = None,
) -> CrawlResult:
    started = time.monotonic() if started is None else started
    return CrawlResult(
        requested_url=request.requested_url,
        success=False,
        status_code=status_code,
        error=CrawlError(code=code, message=message),
        provenance=_metadata(request, tool_version, started),
    )


class MockCrawl4AITool:
    """Deterministic demo tool; it never accesses a network."""

    tool_name = "crawl4ai"
    tool_version = "mock-1.0"

    def __init__(self, responses: Mapping[str, CrawlResult] | None = None) -> None:
        self.responses = dict(responses or {})
        self.requests: list[CrawlRequest] = []

    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        started = time.monotonic()
        try:
            url, domain = DomainPolicy(request.allowed_domains).validate(request.requested_url)
        except DisallowedCrawlDomain as error:
            return error_result(request, CrawlErrorCode.DOMAIN_NOT_ALLOWED, str(error),
                                tool_version=self.tool_version, started=started)
        except ValueError as error:
            return error_result(request, CrawlErrorCode.INVALID_URL, str(error),
                                tool_version=self.tool_version, started=started)
        self.requests.append(request)
        supplied = self.responses.get(url)
        if supplied is not None:
            result = supplied.model_copy(deep=True)
            if result.raw_content and len(result.raw_content) > request.max_content_chars:
                result.raw_content = result.raw_content[:request.max_content_chars]
                result.provenance.content_truncated = True
            return result
        title = "Demo Technical Projects — Mock Crawl4AI Page"
        content = (
            "DEMO DATA — generated locally for tests; this is not a real student profile.\n"
            "A public technical project directory with example entries for computer vision, "
            "natural language processing, and machine learning projects.\n"
        )
        truncated = len(content) > request.max_content_chars
        content = content[:request.max_content_chars]
        return CrawlResult(
            requested_url=request.requested_url,
            final_url=url,
            status_code=200,
            page_title=title,
            raw_content=content,
            success=True,
            provenance=_metadata(request, self.tool_version, started, url, domain, truncated=truncated),
        )


class RealCrawl4AITool:
    """Lazy adapter around the Crawl4AI SDK; policy is checked before networking."""

    tool_name = "crawl4ai"

    def __init__(self) -> None:
        try:
            self.tool_version = importlib.metadata.version("crawl4ai")
        except importlib.metadata.PackageNotFoundError:
            self.tool_version = "not-installed"

    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        started = time.monotonic()
        try:
            normalized_url, domain = DomainPolicy(request.allowed_domains).validate(request.requested_url)
        except DisallowedCrawlDomain as error:
            return error_result(request, CrawlErrorCode.DOMAIN_NOT_ALLOWED, str(error),
                                tool_version=self.tool_version, started=started)
        except ValueError as error:
            return error_result(request, CrawlErrorCode.INVALID_URL, str(error),
                                tool_version=self.tool_version, started=started)

        try:
            # Keep Crawl4AI imports at this adapter boundary so mock-mode users/tests
            # do not import the SDK or initialize a browser.
            from app.core.config import settings

            if settings.crawl4ai_base_directory:
                os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", settings.crawl4ai_base_directory)
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        except Exception as error:
            return error_result(request, CrawlErrorCode.TOOL_FAILURE,
                                f"Crawl4AI SDK could not be initialized: {error}",
                                tool_version=self.tool_version, started=started)

        try:
            run_config = CrawlerRunConfig(
                check_robots_txt=True,
                page_timeout=int(request.timeout_seconds * 1000),
                exclude_external_links=False,
            )
            crawler = AsyncWebCrawler()
            blocked_urls: list[str] = []

            async def route_allowlisted_requests(page, browser_context, **kwargs):
                async def enforce_domain_policy(route):
                    try:
                        DomainPolicy(request.allowed_domains).validate(route.request.url)
                    except ValueError:
                        blocked_urls.append(route.request.url)
                        await route.abort()
                    else:
                        await route.continue_()

                await browser_context.route("**/*", enforce_domain_policy)
                return page

            crawler.crawler_strategy.set_hook("on_page_context_created", route_allowlisted_requests)
            try:
                await crawler.start()
                raw = await crawler.arun(url=normalized_url, config=run_config)
            finally:
                await crawler.close()
        except TimeoutError as error:
            return error_result(request, CrawlErrorCode.TIMEOUT, str(error) or "Crawl timed out",
                                tool_version=self.tool_version, started=started)
        except ImportError as error:
            return error_result(request, CrawlErrorCode.TOOL_FAILURE,
                                f"Crawl4AI is unavailable: {error}", tool_version=self.tool_version,
                                started=started)
        except Exception as error:
            name = type(error).__name__.lower()
            code = CrawlErrorCode.TIMEOUT if "timeout" in name else CrawlErrorCode.CONNECTION_FAILURE
            return error_result(request, code, str(error), tool_version=self.tool_version, started=started)

        try:
            status_code = getattr(raw, "status_code", None)
            final_url = getattr(raw, "redirected_url", None) or getattr(raw, "url", None) or normalized_url
            _, final_domain = DomainPolicy(request.allowed_domains).validate(final_url)
            if blocked_urls and not bool(getattr(raw, "success", False)):
                return error_result(request, CrawlErrorCode.DOMAIN_NOT_ALLOWED,
                                    "A navigation outside the allowlist was blocked before it was requested.",
                                    status_code=status_code, tool_version=self.tool_version, started=started)
            if status_code == 429:
                return error_result(request, CrawlErrorCode.RATE_LIMITED,
                                    "Source returned HTTP 429; no retry or circumvention was attempted.",
                                    status_code=429, tool_version=self.tool_version, started=started)
            if status_code in {401, 403}:
                return error_result(request, CrawlErrorCode.ACCESS_RESTRICTED,
                                    "Source denied access or robots policy disallowed crawling.",
                                    status_code=status_code, tool_version=self.tool_version, started=started)
            # Prefer the crawler's readable representation: raw HTML is often a
            # single minified line and defeats evidence-preserving line extraction.
            markdown = getattr(raw, "markdown", None)
            raw_content = getattr(markdown, "raw_markdown", markdown)
            if not isinstance(raw_content, str) or not raw_content:
                raw_content = getattr(raw, "cleaned_html", None)
            if not isinstance(raw_content, str) or not raw_content:
                raw_content = getattr(raw, "html", None)
            if raw_content is not None and not isinstance(raw_content, str):
                raw_content = str(raw_content)
            if not isinstance(raw_content, str):
                raw_content = ""
            truncated = len(raw_content) > request.max_content_chars
            raw_content = raw_content[:request.max_content_chars]
            links = getattr(raw, "links", None) or {}
            discovered = _extract_links(links)
            success = bool(getattr(raw, "success", False))
            error_message = getattr(raw, "error_message", None)
            if status_code is not None and status_code >= 400:
                if status_code == 429:
                    code = CrawlErrorCode.RATE_LIMITED
                elif status_code in {401, 403}:
                    code = CrawlErrorCode.ACCESS_RESTRICTED
                else:
                    code = CrawlErrorCode.HTTP_ERROR
                return error_result(request, code, error_message or f"HTTP status {status_code}",
                                    status_code=status_code, tool_version=self.tool_version, started=started)
            if not success:
                if status_code == 429:
                    code = CrawlErrorCode.RATE_LIMITED
                elif status_code in {401, 403}:
                    code = CrawlErrorCode.ACCESS_RESTRICTED
                else:
                    code = CrawlErrorCode.TOOL_FAILURE
                return error_result(request, code, error_message or "Crawl4AI reported a failed crawl",
                                    status_code=status_code, tool_version=self.tool_version, started=started)
            return CrawlResult(
                requested_url=request.requested_url,
                final_url=final_url,
                status_code=status_code,
                page_title=_page_title(getattr(raw, "metadata", None)),
                raw_content=raw_content,
                discovered_links=discovered,
                success=True,
                provenance=_metadata(request, self.tool_version, started, final_url, final_domain,
                                     truncated=truncated),
            )
        except DisallowedCrawlDomain as error:
            return error_result(request, CrawlErrorCode.DOMAIN_NOT_ALLOWED,
                                f"Redirect target rejected by domain policy: {error}",
                                status_code=getattr(raw, "status_code", None),
                                tool_version=self.tool_version, started=started)
        except Exception as error:
            return error_result(request, CrawlErrorCode.MALFORMED_RESULT,
                                f"Crawl4AI returned a malformed result: {error}",
                                status_code=getattr(raw, "status_code", None),
                                tool_version=self.tool_version, started=started)


def _page_title(metadata: object) -> str | None:
    return metadata.get("title") if isinstance(metadata, dict) else None


def _extract_links(links: object) -> list[str]:
    if not isinstance(links, dict):
        return []
    found: list[str] = []
    for link_group in links.values():
        if not isinstance(link_group, list):
            continue
        for link in link_group:
            if isinstance(link, dict) and isinstance(link.get("href"), str):
                found.append(link["href"])
    return found
