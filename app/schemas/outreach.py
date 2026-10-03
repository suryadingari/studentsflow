"""Transport-neutral outreach, consent, event, and follow-up models."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas.email import EmailDraft


class ProviderStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    TEMPORARY_FAILURE = "temporary_failure"
    PERMANENT_FAILURE = "permanent_failure"


class ProviderMessage(BaseModel):
    draft_id: str
    approved_version: int
    idempotency_key: str
    recipient: str
    subject: str
    body: str


class ProviderResult(BaseModel):
    provider_message_id: str | None = None
    status: ProviderStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    recipient: str
    subject: str
    error: str | None = None
    provider_name: str = "abstract"


class OutreachEventType(StrEnum):
    SEND_REQUESTED = "send_requested"
    SENT = "sent"
    FAILED = "failed"
    TEMPORARY_FAILURE = "temporary_failure"
    PERMANENT_FAILURE = "permanent_failure"
    BOUNCED = "bounced"
    DELIVERED = "delivered"
    REPLY_RECEIVED = "reply_received"
    OPTED_OUT = "opted_out"
    DUPLICATE_SEND = "duplicate_send"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    FOLLOW_UP_PLANNED = "follow_up_planned"
    FOLLOW_UP_CANCELLED = "follow_up_cancelled"


class OutreachEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    outreach_id: str
    event_type: OutreachEventType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str | None = None
    provider_event_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutreachErrorCode(StrEnum):
    NOT_APPROVED = "not_approved"
    OPTED_OUT = "opted_out"
    RATE_LIMITED = "rate_limited"
    DUPLICATE_SEND = "duplicate_send"
    PROVIDER_FAILURE = "provider_failure"
    TEMPORARY_PROVIDER_FAILURE = "temporary_provider_failure"
    PERMANENT_PROVIDER_FAILURE = "permanent_provider_failure"
    INVALID_RECIPIENT = "invalid_recipient"
    SEND_IN_PROGRESS = "send_in_progress"


class OutreachStatus(StrEnum):
    SENT = "sent"
    DUPLICATE_SEND = "duplicate_send"
    BLOCKED = "blocked"
    OPTED_OUT = "opted_out"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    TEMPORARY_FAILURE = "temporary_failure"
    PERMANENT_FAILURE = "permanent_failure"
    IN_PROGRESS = "in_progress"


class OutreachRequest(BaseModel):
    draft: EmailDraft
    campaign_id: str | None = None


class OutreachResult(BaseModel):
    outreach_id: str
    idempotency_key: str
    status: OutreachStatus
    error_code: OutreachErrorCode | None = None
    explanation: str
    provider_result: ProviderResult | None = None
    provider_attempts: int = 0
    retry_count: int = 0
    events: list[OutreachEvent] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status in {OutreachStatus.SENT, OutreachStatus.DUPLICATE_SEND}


class OptOutRecord(BaseModel):
    recipient: str
    opted_out: bool = True
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None
    reason: str | None = None


class OptOutRequest(BaseModel):
    recipient: str
    source: str | None = None
    reason: str | None = None


class ReplyStatus(StrEnum):
    REPLIED = "replied"
    NO_REPLY = "no_reply"
    OPTED_OUT = "opted_out"


class ReplyRecord(BaseModel):
    outreach_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutreachState(BaseModel):
    outreach_id: str
    workflow_id: str | None = None
    idempotency_key: str
    draft_id: str
    approved_version: int
    recipient: str
    campaign_id: str | None = None
    status: OutreachStatus
    sent_at: datetime | None = None
    replied_at: datetime | None = None
    opted_out_at: datetime | None = None
    campaign_cancelled: bool = False
    follow_up_count: int = 0
    provider_result: ProviderResult | None = None


class FollowUpStatus(StrEnum):
    NOT_DUE = "not_due"
    DUE = "due"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class FollowUpPolicy(BaseModel):
    delay_hours: int = Field(default=168, ge=1, le=8760)
    max_follow_ups: int = Field(default=1, ge=0, le=5)


class FollowUpRequest(BaseModel):
    outreach_id: str
    now: datetime | None = None


class FollowUpPlan(BaseModel):
    plan_id: str
    original_outreach_id: str
    recipient: str
    due_at: datetime | None = None
    follow_up_number: int
    reason: str
    status: FollowUpStatus


class FollowUpResult(BaseModel):
    plan: FollowUpPlan | None = None
    explanation: str
    reply_status: ReplyStatus = ReplyStatus.NO_REPLY

    @property
    def success(self) -> bool:
        return self.plan is not None
