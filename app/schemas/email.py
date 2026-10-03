"""Email drafts and human approval records; sending is deliberately out of scope."""

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, field_validator

from app.schemas.canonical import CanonicalStudent
from app.schemas.matching import CandidateMatch, UserRequirement
from app.schemas.student import Evidence


class EmailDraftStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class ApprovalActionType(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class EmailRecipient(BaseModel):
    email: str
    evidence_id: str
    source_url: str | None = None
    source_access: str | None = None

    @field_validator("email")
    @classmethod
    def require_email_shape(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("recipient must be a valid supplied email address")
        return value


class EmailEvidenceReference(BaseModel):
    criterion_type: str
    evidence_id: str
    source_url: str
    supporting_text: str
    claim_value: Any


class EmailPersonalizationPoint(BaseModel):
    criterion_type: str
    statement: str
    evidence_references: list[EmailEvidenceReference] = Field(min_length=1)


class EmailDraftRequest(BaseModel):
    request_id: str | None = None
    requirement: UserRequirement
    candidate: CanonicalStudent
    match: CandidateMatch
    recipient: EmailRecipient | None = None
    sender_name: str | None = None
    signature: str = "Research Team"


class EmailDraftVersion(BaseModel):
    version_number: int = Field(ge=1)
    subject: str
    body: str
    authored_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence_references: list[EmailEvidenceReference] = Field(default_factory=list)


class ApprovalAction(BaseModel):
    action: ApprovalActionType
    actor_id: str = Field(min_length=1)
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    subject: str | None = None
    body: str | None = None
    reason: str | None = None

    @field_validator("actor_id")
    @classmethod
    def actor_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("actor_id must not be blank")
        return value.strip()


class ApprovalRecord(BaseModel):
    action_id: str
    action: ApprovalActionType
    actor_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version_number: int
    reason: str | None = None


class EmailDraft(BaseModel):
    draft_id: str
    request_fingerprint: str
    candidate_id: str
    recipient: EmailRecipient | None = None
    generated_subject: str | None = None
    generated_body: str | None = None
    subject: str = ""
    body: str = ""
    greeting: str = ""
    call_to_action: str = ""
    signature: str = ""
    personalization_summary: list[str] = Field(default_factory=list)
    personalization_points: list[EmailPersonalizationPoint] = Field(default_factory=list)
    evidence_references: list[EmailEvidenceReference] = Field(default_factory=list)
    status: EmailDraftStatus
    blocked_reason: str | None = None
    created_by: str = "email-agent"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    versions: list[EmailDraftVersion] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    approved_subject: str | None = None
    approved_body: str | None = None

    @computed_field
    @property
    def success(self) -> bool:
        return self.status != EmailDraftStatus.BLOCKED


class EmailDraftResult(BaseModel):
    draft: EmailDraft
    created: bool

    @computed_field
    @property
    def success(self) -> bool:
        return self.draft.success


class EmailApprovalRequest(BaseModel):
    draft_id: str
    action: ApprovalAction
