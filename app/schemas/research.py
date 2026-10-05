from datetime import datetime, timezone

from pydantic import BaseModel, Field
from app.crawling.models import CrawlConfiguration, SourceCandidate, CrawlResult, SourceDiscoveryOutput
from app.schemas.student import ExtractedStudentInformation, StudentValidationResult, Evidence

class ResearchMetadata(BaseModel):
    iterations: int = 0
    sources_tried: int = 0
    pages_crawled: int = 0
    duration_seconds: float = 0.0

class ResearchRequest(BaseModel):
    requirement: str
    current_year: int = Field(default_factory=lambda: datetime.now(timezone.utc).year, ge=2000, le=2200)
    permitted_domains: frozenset[str] = Field(default_factory=frozenset)
    candidates: list[SourceCandidate] = Field(default_factory=list)
    search_enabled: bool = False
    max_search_results: int = Field(default=10, ge=1, le=25)
    crawl_configuration: CrawlConfiguration = Field(default_factory=CrawlConfiguration)

    max_iterations: int = Field(default=3, ge=1, le=10)
    max_sources: int = Field(default=20, ge=1, le=50)
    max_pages: int = Field(default=50, ge=1, le=100)
    max_duration_seconds: int = Field(default=300, ge=1, le=900)

class ResearchOutput(BaseModel):
    source_discovery: SourceDiscoveryOutput | None = None
    extracted_profiles: list[ExtractedStudentInformation] = Field(default_factory=list)
    validated_candidates: list[StudentValidationResult] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    crawl_results: list[CrawlResult] = Field(default_factory=list)
    metadata: ResearchMetadata = Field(default_factory=ResearchMetadata)
    errors: list[str] = Field(default_factory=list)
    retry_count: int = 0
