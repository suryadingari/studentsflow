"""Process-local orchestration contracts for the Step 10 workflow."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.crawling.models import (CrawlConfiguration, CrawlResult, SourceCandidate,
                                 SourceDiscoveryOutput)
from app.schemas.canonical import CanonicalStudent, DeduplicationDecision
from app.schemas.email import EmailDraft
from app.schemas.matching import CandidateMatch, UserRequirement
from app.schemas.outreach import FollowUpResult, OutreachResult
from app.schemas.student import ExtractedStudentInformation, StudentValidationResult


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStepType(StrEnum):
    SOURCE_DISCOVERY = "source_discovery"
    STUDENT_CRAWLING = "student_crawling"
    EXTRACTION = "extraction"
    VALIDATION = "validation"
    DEDUPLICATION = "deduplication"
    ENRICHMENT = "enrichment"
    MATCHING = "matching"
    EMAIL_DRAFTING = "email_drafting"
    HUMAN_APPROVAL = "human_approval"
    OUTREACH = "outreach"
    FOLLOW_UP = "follow_up"


class WorkflowStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class WorkflowRequest(BaseModel):
    requirement: UserRequirement
    sources: list[SourceCandidate] = Field(default_factory=list)
    permitted_domains: frozenset[str] = Field(default_factory=frozenset)
    crawl_configuration: CrawlConfiguration = Field(default_factory=CrawlConfiguration)
    current_year: int = Field(default_factory=lambda: now_utc().year)
    campaign_id: str | None = None
    signature: str = "Research Team"
    demo_mode: bool = True
    permitted_tools: frozenset[str] = Field(default_factory=lambda: frozenset({
        "crawl4ai", "email_provider", "opt_out_service", "outreach_rate_limiter",
        "outreach_event_store",
    }))
    max_hops: int = Field(default=100, ge=1)


class WorkflowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    step_type: WorkflowStepType
    logical_item_id: str = "workflow"
    agent_name: str | None = None
    agent_version: str | None = None
    execution_id: str | None = None
    duration_seconds: float | None = None
    state: WorkflowStepStatus = WorkflowStepStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    input_reference: str | None = None
    output_reference: str | None = None
    input_metadata: dict[str, Any] = Field(default_factory=dict)
    output_metadata: dict[str, Any] = Field(default_factory=dict)
    error_category: str | None = None
    error: str | None = None
    retry_count: int = 0


class WorkflowMetrics(BaseModel):
    total_duration_seconds: float = 0.0
    steps_total: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    steps_retried: int = 0
    candidates_discovered: int = 0
    candidates_validated: int = 0
    candidates_matched: int = 0
    emails_drafted: int = 0
    emails_approved: int = 0
    emails_sent: int = 0
    follow_ups_planned: int = 0


class WorkflowResult(BaseModel):
    workflow_id: str
    requirement_id: str
    status: WorkflowStatus
    current_step: WorkflowStepType | None = None
    steps: list[WorkflowStep] = Field(default_factory=list)
    pending_draft_ids: list[str] = Field(default_factory=list)
    drafts: list[EmailDraft] = Field(default_factory=list)
    source_discovery: SourceDiscoveryOutput | None = None
    crawl_results: list[CrawlResult] = Field(default_factory=list)
    extracted_profiles: list[ExtractedStudentInformation] = Field(default_factory=list)
    validated_profiles: list[StudentValidationResult] = Field(default_factory=list)
    canonical_students: list[CanonicalStudent] = Field(default_factory=list)
    deduplication_decisions: list[DeduplicationDecision] = Field(default_factory=list)
    candidate_matches: list[CandidateMatch] = Field(default_factory=list)
    outreach_results: list[OutreachResult] = Field(default_factory=list)
    outreach_draft_ids: dict[str, str] = Field(default_factory=dict)
    follow_up_results: list[FollowUpResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    cancellation_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    metrics: WorkflowMetrics = Field(default_factory=WorkflowMetrics)
    agent_versions: dict[str, str] = Field(default_factory=dict)
