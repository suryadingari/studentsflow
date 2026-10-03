from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.core.config import settings
from app.crawling.policy import DomainPolicy


class SourceType(StrEnum):
    PUBLIC_PROFILE = "public_profile"
    PORTFOLIO = "portfolio"
    CODE_REPOSITORY = "code_repository"
    RESEARCH_PUBLICATION = "research_publication"
    UNIVERSITY_PAGE = "university_page"
    ORGANIZATION_PAGE = "organization_page"
    OTHER = "other"


class SourceAccess(StrEnum):
    PUBLIC = "public"
    AUTHORIZED = "authorized"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class SourceCandidate(BaseModel):
    url: str
    domain: str
    source_type: SourceType
    reason: str = Field(min_length=1)
    access: SourceAccess
    discovery_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def url_domain_must_match(self) -> "SourceCandidate":
        _, actual_domain = DomainPolicy.parse_url(self.url)
        supplied_domain = DomainPolicy._normalize_domain(self.domain)
        if supplied_domain != actual_domain:
            raise ValueError("domain must match the hostname in url")
        return self

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


class RejectedSource(BaseModel):
    url: str
    reason: str


class SourceDiscoveryRequest(BaseModel):
    requirement: str = Field(min_length=1)
    candidates: list[SourceCandidate] = Field(default_factory=list)
    permitted_domains: frozenset[str] = Field(default_factory=frozenset)


class SourceDiscoveryOutput(BaseModel):
    candidates: list[SourceCandidate] = Field(default_factory=list)
    rejected: list[RejectedSource] = Field(default_factory=list)
    note: str = "Source candidates do not establish facts about any person."


class CrawlConfiguration(BaseModel):
    allowed_domains: frozenset[str] = Field(default_factory=lambda: settings.allowed_domain_set)
    max_pages: int = Field(default=5, ge=1, le=25)
    max_depth: int = Field(default=1, ge=0, le=5)
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    min_request_interval_seconds: float = Field(default=1.0, ge=0, le=60)
    max_content_chars: int = Field(default=500_000, ge=1, le=2_000_000)
    max_crawl_hops: int = Field(default=5, ge=1, le=25)


class StudentCrawlerRequest(BaseModel):
    source_url: str
    permitted_domains: frozenset[str] = Field(default_factory=frozenset)
    configuration: CrawlConfiguration = Field(default_factory=CrawlConfiguration)

    @field_validator("source_url")
    @classmethod
    def source_url_must_be_http(cls, value: str) -> str:
        DomainPolicy.parse_url(value)
        return value


class CrawlRequest(BaseModel):
    requested_url: str
    allowed_domains: frozenset[str]
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    max_content_chars: int = Field(default=500_000, ge=1, le=2_000_000)

    @model_validator(mode="after")
    def request_url_must_be_allowlisted(self) -> "CrawlRequest":
        DomainPolicy(self.allowed_domains).validate(self.requested_url)
        return self


class CrawlErrorCode(StrEnum):
    INVALID_URL = "invalid_url"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    TIMEOUT = "timeout"
    CONNECTION_FAILURE = "connection_failure"
    RATE_LIMITED = "rate_limited"
    ACCESS_RESTRICTED = "access_restricted"
    HTTP_ERROR = "http_error"
    TOOL_FAILURE = "tool_failure"
    MALFORMED_RESULT = "malformed_result"
    HOP_LIMIT_EXCEEDED = "hop_limit_exceeded"
    PERMISSION_DENIED = "permission_denied"


class CrawlError(BaseModel):
    code: CrawlErrorCode
    message: str
    retryable: bool = False


class CrawlMetadata(BaseModel):
    source_url: str
    final_url: str | None = None
    domain: str | None = None
    tool_name: str = "crawl4ai"
    tool_version: str
    crawl_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0
    depth: int = 0
    content_truncated: bool = False


class CrawlResult(BaseModel):
    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    page_title: str | None = None
    raw_content: str | None = None
    discovered_links: list[str] = Field(default_factory=list)
    success: bool
    error: CrawlError | None = None
    provenance: CrawlMetadata

    @model_validator(mode="after")
    def success_and_error_must_agree(self) -> "CrawlResult":
        if self.success and self.error is not None:
            raise ValueError("successful crawl results cannot include an error")
        if not self.success and self.error is None:
            raise ValueError("failed crawl results must include a structured error")
        if self.provenance.source_url != self.requested_url:
            raise ValueError("provenance source_url must preserve requested_url")
        if self.final_url and self.provenance.final_url and self.final_url != self.provenance.final_url:
            raise ValueError("provenance final_url must match the crawl result final_url")
        return self


class StudentCrawlerOutput(BaseModel):
    results: list[CrawlResult] = Field(default_factory=list)
    stopped_reason: str | None = None

    @computed_field
    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)

    @computed_field
    @property
    def pages_crawled(self) -> int:
        return sum(result.success for result in self.results)

    @computed_field
    @property
    def error_summary(self) -> str | None:
        if self.stopped_reason:
            return self.stopped_reason
        return next((result.error.message for result in self.results if result.error), None)
