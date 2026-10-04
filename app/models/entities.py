"""Relational persistence models for workflow execution and its provenance."""

from datetime import datetime, timezone

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RequirementRecord(Base):
    __tablename__ = "requirements"

    requirement_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    criteria_json: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class UserAccountRecord(Base):
    __tablename__ = "user_accounts"
    __table_args__ = (CheckConstraint("role IN ('user','admin')", name="ck_user_accounts_role"),)

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(190), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class WorkflowRecord(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint("status IN ('created','running','waiting_for_approval','paused','completed','failed','cancelled')", name="ck_workflows_status"),
        Index("ix_workflows_status_created", "status", "created_at"),
    )

    workflow_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("user_accounts.user_id", ondelete="SET NULL"), index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.requirement_id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    runtime_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class WorkflowStepRecord(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_id", "step_type", "logical_item_id", name="uq_workflow_step_identity"),
        CheckConstraint("state IN ('pending','running','waiting','succeeded','failed','skipped','blocked')", name="ck_workflow_steps_state"),
        Index("ix_workflow_steps_workflow_state", "workflow_id", "state"),
    )

    step_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False)
    step_type: Mapped[str] = mapped_column(String(40), nullable=False)
    logical_item_id: Mapped[str] = mapped_column(String(500), nullable=False, default="workflow")
    agent_name: Mapped[str | None] = mapped_column(String(100))
    agent_version: Mapped[str | None] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 4))
    input_reference: Mapped[str | None] = mapped_column(String(500))
    output_reference: Mapped[str | None] = mapped_column(String(500))
    input_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_category: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AgentRunRecord(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_agent_started", "agent_name", "started_at"),)

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_steps.step_id", ondelete="CASCADE"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 4))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_category: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class StudentRecord(Base):
    __tablename__ = "students"
    __table_args__ = (Index("ix_students_status", "final_year_status", "ai_interest_status"),)

    student_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    final_year_status: Mapped[str] = mapped_column(String(40), nullable=False)
    ai_interest_status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class StudentSourceRecord(Base):
    __tablename__ = "student_sources"
    __table_args__ = (CheckConstraint("access IN ('public','authorized','restricted','unavailable','unknown')", name="ck_student_source_access"),
                      UniqueConstraint("student_id", "source_url", name="uq_student_source"),
                      Index("ix_student_sources_url", "source_url"))

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    access: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentEvidenceRecord(Base):
    __tablename__ = "student_evidence"
    __table_args__ = (Index("ix_student_evidence_student_claim", "student_id", "claim_type"),)

    evidence_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("student_sources.source_id", ondelete="RESTRICT"), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence_category: Mapped[str | None] = mapped_column(String(50))
    claim_value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    supporting_text: Mapped[str] = mapped_column(Text, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    strength: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeduplicationRecord(Base):
    __tablename__ = "deduplication_records"

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    left_student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    right_student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class MatchRecord(Base):
    __tablename__ = "matches"
    __table_args__ = (CheckConstraint("status IN ('match','partial_match','no_match','insufficient_evidence')", name="ck_matches_status"),
                      Index("ix_matches_requirement_status", "requirement_id", "status"))

    match_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.requirement_id", ondelete="RESTRICT"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    assessment_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_references_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class EmailDraftRecord(Base):
    __tablename__ = "email_drafts"
    __table_args__ = (CheckConstraint("status IN ('draft','pending_review','approved','edited','rejected','blocked')", name="ck_email_drafts_status"),
                      Index("ix_email_drafts_status_created", "status", "created_at"),
                      Index("ix_email_drafts_recipient", "recipient"))

    draft_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[str | None] = mapped_column(ForeignKey("students.student_id", ondelete="SET NULL"), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(320))
    recipient_evidence_id: Mapped[str | None] = mapped_column(ForeignKey("student_evidence.evidence_id", ondelete="SET NULL"))
    recipient_source_url: Mapped[str | None] = mapped_column(Text)
    draft_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailDraftVersionRecord(Base):
    __tablename__ = "email_draft_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version_number", name="uq_email_draft_version"),)

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("email_drafts.draft_id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    authored_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_references_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ApprovalRecordModel(Base):
    __tablename__ = "approval_records"
    __table_args__ = (CheckConstraint("action IN ('approve','edit','reject')", name="ck_approval_action"),)

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("email_drafts.draft_id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class OutreachRecord(Base):
    __tablename__ = "outreach_records"
    __table_args__ = (CheckConstraint("status IN ('sent','duplicate_send','blocked','opted_out','rate_limited','failed','temporary_failure','permanent_failure','in_progress')", name="ck_outreach_status"),
                      UniqueConstraint("idempotency_key", name="uq_outreach_idempotency_key"),
                      Index("ix_outreach_status_created", "status", "created_at"),
                      Index("ix_outreach_recipient", "recipient"))

    outreach_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("email_drafts.draft_id", ondelete="RESTRICT"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    provider_result_json: Mapped[dict | None] = mapped_column(JSON)
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutreachEventRecord(Base):
    __tablename__ = "outreach_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    outreach_id: Mapped[str] = mapped_column(ForeignKey("outreach_records.outreach_id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_event_type: Mapped[str | None] = mapped_column(String(120))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class OptOutRecordModel(Base):
    __tablename__ = "opt_outs"

    recipient: Mapped[str] = mapped_column(String(320), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)


class ReplyRecordModel(Base):
    __tablename__ = "replies"

    outreach_id: Mapped[str] = mapped_column(ForeignKey("outreach_records.outreach_id", ondelete="CASCADE"), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class FollowUpPlanRecord(Base):
    __tablename__ = "follow_up_plans"
    __table_args__ = (CheckConstraint("status IN ('not_due','due','cancelled','completed')", name="ck_followup_status"),
                      UniqueConstraint("outreach_id", "follow_up_number", name="uq_followup_outreach_number"),
                      Index("ix_follow_up_status_due", "status", "due_at"))

    plan_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    outreach_id: Mapped[str] = mapped_column(ForeignKey("outreach_records.outreach_id", ondelete="CASCADE"), nullable=False, index=True)
    follow_up_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_workflow_time", "workflow_id", "called_at"),)

    tool_call_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_steps.step_id", ondelete="SET NULL"))
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 4))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_event_time", "event_type", "occurred_at"),)

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="SET NULL"), index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_steps.step_id", ondelete="SET NULL"), index=True)
    execution_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class KPIMetricRecord(Base):
    __tablename__ = "kpi_metrics"
    __table_args__ = (UniqueConstraint("workflow_id", "metric_name", name="uq_workflow_kpi"),
                      Index("ix_kpi_metric_recorded", "metric_name", "recorded_at"))

    metric_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(16, 4), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


__all__ = [
    "AgentRunRecord", "ApprovalRecordModel", "AuditLogRecord", "DeduplicationRecord",
    "EmailDraftRecord", "EmailDraftVersionRecord", "FollowUpPlanRecord", "KPIMetricRecord",
    "MatchRecord", "OptOutRecordModel", "OutreachEventRecord", "OutreachRecord",
    "ReplyRecordModel", "RequirementRecord", "StudentEvidenceRecord", "StudentRecord", "StudentSourceRecord",
    "ToolCallRecord", "UserAccountRecord", "WorkflowRecord", "WorkflowStepRecord",
]
