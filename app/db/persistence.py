"""SQLAlchemy repositories, snapshot projection, and durable audit/KPI queries."""

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.events import AgentEvent
from app.models import (AgentRunRecord, ApprovalRecordModel, AuditLogRecord,
                        DeduplicationRecord, EmailDraftRecord, EmailDraftVersionRecord,
                        FollowUpPlanRecord, KPIMetricRecord, MatchRecord, OptOutRecordModel,
                        OutreachEventRecord, OutreachRecord, RequirementRecord,
                        StudentEvidenceRecord, StudentRecord, StudentSourceRecord,
                        ToolCallRecord, WorkflowRecord, WorkflowStepRecord)
from app.schemas.workflow import WorkflowRequest, WorkflowResult
from app.schemas.workflow import WorkflowStatus


_SECRET_KEYS = {"password", "token", "secret", "api_key", "authorization", "email", "recipient",
                "body", "subject", "raw_content"}


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return json.loads(json.dumps(value, default=lambda obj: obj.isoformat()
                                 if isinstance(obj, datetime) else str(obj)))


def _key(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:28]


async def _existing_rows(session: AsyncSession, model: Any, key_name: str,
                         keys: list[str]) -> dict[str, Any]:
    """Fetch projected rows in bounded batches instead of issuing N+1 lookups."""
    unique_keys = list(dict.fromkeys(keys))
    found: dict[str, Any] = {}
    column = getattr(model, key_name)
    for start in range(0, len(unique_keys), 500):
        batch = unique_keys[start:start + 500]
        rows = (await session.scalars(select(model).where(column.in_(batch)))).all()
        found.update((getattr(row, key_name), row) for row in rows)
    return found


def _safe_details(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _safe_details(item) for key, item in value.items()
                if not any(secret in key.lower() for secret in _SECRET_KEYS)}
    if isinstance(value, (list, tuple)):
        return [_safe_details(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class DatabaseConfigurationError(RuntimeError):
    """Raised when a database operation is attempted without a usable URL."""


def validate_database_url(database_url: str) -> str:
    value = database_url.strip()
    if not value:
        raise DatabaseConfigurationError("DATABASE_URL is missing; configure it in the environment or .env.")
    lowered = value.lower()
    placeholders = ("username:password@", "/database_name", "your_password", "changeme")
    if not lowered.startswith(("postgresql+psycopg://", "postgresql://")):
        raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL with the psycopg driver.")
    if any(token in lowered for token in placeholders):
        raise DatabaseConfigurationError("DATABASE_URL still contains placeholder credentials/database values.")
    try:
        parsed = make_url(value)
    except Exception as error:
        raise DatabaseConfigurationError("DATABASE_URL is not a valid SQLAlchemy PostgreSQL URL.") from error
    if not parsed.host or not parsed.database:
        raise DatabaseConfigurationError("DATABASE_URL must include a PostgreSQL host and database name.")
    return value


def validate_storage_url(database_url: str, *, allow_sqlite: bool = False) -> str:
    """Validate an application persistence URL, optionally for the local adapter."""
    value = (database_url or "").strip()
    if allow_sqlite and value.startswith("sqlite+aiosqlite:///"):
        try:
            parsed = make_url(value)
        except Exception as error:
            raise DatabaseConfigurationError("Local SQLite URL is malformed.") from error
        if not parsed.database:
            raise DatabaseConfigurationError("Local SQLite URL must name a database file.")
        return value
    return validate_database_url(value)


class PostgresWorkflowRepository:
    """Persists restartable workflow snapshots and normalized projections."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], database_url: str) -> None:
        self.sessions = sessions
        self.database_url = database_url

    def ensure_configured(self) -> None:
        validate_storage_url(self.database_url, allow_sqlite=True)

    async def save_workflow(self, result: WorkflowResult, request: WorkflowRequest,
                            *, hop_count: int = 0) -> None:
        self.ensure_configured()
        payload = result.model_dump(mode="json")
        request_data = request.model_dump(mode="json")
        async with self.sessions.begin() as session:
            req = await session.get(RequirementRecord, result.requirement_id)
            req_values = {"raw_text": request.requirement.raw_text,
                          "criteria_json": _json(request.requirement.criteria)}
            if req is None:
                session.add(RequirementRecord(requirement_id=result.requirement_id, **req_values))
            else:
                for name, value in req_values.items():
                    setattr(req, name, value)
            workflow = await session.get(WorkflowRecord, result.workflow_id)
            runtime_state = dict(workflow.runtime_json or {}) if workflow is not None else {}
            synthetic_projection_done = bool(runtime_state.get("synthetic_projection_done"))
            terminal_snapshot = result.status in {
                WorkflowStatus.WAITING_FOR_APPROVAL, WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED, WorkflowStatus.CANCELLED,
            }
            project_synthetic_data = (
                not request.synthetic_demo_dataset
                or (terminal_snapshot and not synthetic_projection_done)
            )
            if request.synthetic_demo_dataset and terminal_snapshot:
                runtime_state["synthetic_projection_done"] = True
            values = {
                "owner_id": request.owner_id,
                "requirement_id": result.requirement_id, "status": result.status.value,
                "current_step": result.current_step.value if result.current_step else None,
                "created_at": result.created_at, "updated_at": result.updated_at,
                "request_json": request_data, "result_json": payload,
                "runtime_json": {**runtime_state, "hop_count": hop_count},
            }
            if workflow is None:
                session.add(WorkflowRecord(workflow_id=result.workflow_id, **values))
            else:
                for name, value in values.items():
                    setattr(workflow, name, value)
            existing_steps = await _existing_rows(
                session, WorkflowStepRecord, "step_id", [step.step_id for step in result.steps])
            existing_runs = await _existing_rows(
                session, AgentRunRecord, "execution_id",
                [step.execution_id for step in result.steps if step.execution_id])
            for step in result.steps:
                row = existing_steps.get(step.step_id)
                step_values = {
                    "workflow_id": step.workflow_id, "step_type": step.step_type.value,
                    "logical_item_id": step.logical_item_id, "agent_name": step.agent_name,
                    "agent_version": step.agent_version, "state": step.state.value,
                    "started_at": step.started_at, "ended_at": step.ended_at,
                    "duration_seconds": step.duration_seconds,
                    "input_reference": step.input_reference, "output_reference": step.output_reference,
                    "input_metadata": _json(step.input_metadata), "output_metadata": _json(step.output_metadata),
                    "error_category": step.error_category, "error": step.error,
                    "retry_count": step.retry_count,
                }
                if row is None:
                    session.add(WorkflowStepRecord(step_id=step.step_id, **step_values))
                else:
                    for name, value in step_values.items():
                        setattr(row, name, value)
                if step.execution_id and step.agent_name and step.agent_version:
                    execution = existing_runs.get(step.execution_id)
                    run_values = {
                        "workflow_id": result.workflow_id, "step_id": step.step_id,
                        "agent_name": step.agent_name, "agent_version": step.agent_version,
                        "status": step.state.value, "started_at": step.started_at or result.updated_at,
                        "ended_at": step.ended_at, "duration_seconds": step.duration_seconds,
                        "retry_count": step.retry_count, "error_category": step.error_category,
                        "error": step.error, "metadata_json": _json(step.output_metadata),
                    }
                    if execution is None:
                        session.add(AgentRunRecord(execution_id=step.execution_id, **run_values))
                    else:
                        for name, value in run_values.items():
                            setattr(execution, name, value)
            if project_synthetic_data:
                await self._project_candidates(session, result)
                await self._project_matches(session, result)
            await self._project_email_and_outreach(session, result)
            await self._project_tools(session, result)
            await self._project_metrics(session, result)

    async def _project_candidates(self, session: AsyncSession, result: WorkflowResult) -> None:
        student_ids = [student.canonical_id for student in result.canonical_students]
        source_keys = [
            _key("source", student.canonical_id, str(source.source_url))
            for student in result.canonical_students for source in student.profile.source_references
        ]
        evidence_keys = [evidence.evidence_id for student in result.canonical_students
                         for evidence in student.profile.evidence]
        students_existing = await _existing_rows(session, StudentRecord, "student_id", student_ids)
        sources_existing = await _existing_rows(session, StudentSourceRecord, "source_id", source_keys)
        evidence_existing = await _existing_rows(session, StudentEvidenceRecord, "evidence_id", evidence_keys)
        evidence_statuses: dict[str, str] = {}
        for validated in result.validated_profiles:
            for claim in [*validated.validated_claims, *validated.unsupported_claims]:
                for evidence_id in claim.evidence_ids:
                    evidence_statuses[evidence_id] = claim.status.value

        for student in result.canonical_students:
            student_id = student.canonical_id
            row = students_existing.get(student_id)
            profile = student.profile
            values = {"workflow_id": result.workflow_id, "profile_json": _json(profile),
                      "final_year_status": profile.final_year_status.value,
                      "ai_interest_status": profile.ai_interest_status.value,
                      "updated_at": result.updated_at}
            if row is None:
                session.add(StudentRecord(student_id=student_id, **values))
            else:
                for name, value in values.items():
                    if name == "workflow_id":
                        continue
                    setattr(row, name, value)
            sources: dict[str, Any] = {}
            for source in profile.source_references:
                sources[str(source.source_url)] = source
            source_ids: dict[str, str] = {}
            for url, source in sources.items():
                source_id = _key("source", student_id, url)
                source_ids[url] = source_id
                record = sources_existing.get(source_id)
                src_values = {"student_id": student_id, "source_url": url,
                              "source_title": source.title,
                              "source_type": source.source_type or source.evidence_type.value,
                              "access": source.access,
                              "observed_at": None}
                if record is None:
                    session.add(StudentSourceRecord(source_id=source_id, **src_values))
                else:
                    for name, value in src_values.items():
                        setattr(record, name, value)
            for evidence in profile.evidence:
                url = str(evidence.source_url)
                source_id = source_ids.get(url)
                if source_id is None:
                    continue
                record = evidence_existing.get(evidence.evidence_id)
                evidence_status = evidence_statuses.get(
                    evidence.evidence_id, evidence.validation_status.value)
                evidence_values = {
                    "student_id": student_id, "source_id": source_id,
                    "claim_type": evidence.claim_type.value,
                    "evidence_category": evidence.ai_category.value if evidence.ai_category else None,
                    "claim_value_json": _json(evidence.claim_value),
                    "supporting_text": evidence.supporting_text,
                    "validation_status": evidence_status,
                    "strength": evidence.strength.value, "observed_at": evidence.extracted_at,
                }
                if record is None:
                    session.add(StudentEvidenceRecord(evidence_id=evidence.evidence_id, **evidence_values))
                else:
                    for name, value in evidence_values.items():
                        setattr(record, name, value)
        dedup_keys = [_key("dedup", result.workflow_id, str(index))
                      for index in range(len(result.deduplication_decisions))]
        dedup_existing = await _existing_rows(session, DeduplicationRecord, "record_id", dedup_keys)
        for index, decision in enumerate(result.deduplication_decisions):
            left = _canonical_for_profile(result.validated_profiles, result.canonical_students,
                                          decision.left_profile_index)
            right = _canonical_for_profile(result.validated_profiles, result.canonical_students,
                                           decision.right_profile_index)
            if left is None or right is None:
                continue
            record_id = dedup_keys[index]
            record = dedup_existing.get(record_id)
            values = {"workflow_id": result.workflow_id, "left_student_id": left,
                      "right_student_id": right, "decision": decision.decision.value,
                      "decision_json": _json(decision)}
            if record is None:
                session.add(DeduplicationRecord(record_id=record_id, **values))
            else:
                for name, value in values.items():
                    setattr(record, name, value)

    async def _project_matches(self, session: AsyncSession, result: WorkflowResult) -> None:
        match_ids = [_key("match", result.workflow_id, match.canonical_student_id)
                     for match in result.candidate_matches]
        existing_matches = await _existing_rows(session, MatchRecord, "match_id", match_ids)
        for match in result.candidate_matches:
            record_id = _key("match", result.workflow_id, match.canonical_student_id)
            row = existing_matches.get(record_id)
            values = {"workflow_id": result.workflow_id,
                      "requirement_id": result.requirement_id,
                      "student_id": match.canonical_student_id,
                      "status": match.status.value, "explanation": match.explanation,
                      "assessment_json": _json(match),
                      "evidence_references_json": [e.model_dump(mode="json")
                                                   for assessment in match.assessments for e in assessment.evidence]}
            if row is None:
                session.add(MatchRecord(match_id=record_id, **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)

    async def _project_email_and_outreach(self, session: AsyncSession, result: WorkflowResult) -> None:
        for draft in result.drafts:
            record = await session.get(EmailDraftRecord, draft.draft_id)
            recipient = draft.recipient
            evidence_id = recipient.evidence_id if recipient else None
            draft_values = {
                "workflow_id": result.workflow_id, "student_id": draft.candidate_id,
                "request_fingerprint": draft.request_fingerprint, "status": draft.status.value,
                "recipient": recipient.email if recipient else None,
                "recipient_evidence_id": evidence_id,
                "recipient_source_url": str(recipient.source_url) if recipient and recipient.source_url else None,
                "draft_json": _json(draft), "created_at": draft.created_at, "updated_at": draft.updated_at,
            }
            if record is None:
                session.add(EmailDraftRecord(draft_id=draft.draft_id, **draft_values))
            else:
                for name, value in draft_values.items():
                    setattr(record, name, value)
            for version in draft.versions:
                version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{draft.draft_id}:{version.version_number}"))
                version_row = await session.get(EmailDraftVersionRecord, version_id)
                version_values = {"draft_id": draft.draft_id, "version_number": version.version_number,
                                  "subject": version.subject, "body": version.body,
                                  "authored_by": version.authored_by, "created_at": version.created_at,
                                  "evidence_references_json": _json(version.evidence_references)}
                if version_row is None:
                    session.add(EmailDraftVersionRecord(version_id=version_id, **version_values))
                else:
                    for name, value in version_values.items():
                        setattr(version_row, name, value)
            for approval in draft.approvals:
                approval_row = await session.get(ApprovalRecordModel, approval.action_id)
                approval_values = {"draft_id": draft.draft_id, "action": approval.action.value,
                                   "actor_id": approval.actor_id, "occurred_at": approval.occurred_at,
                                   "version_number": approval.version_number, "reason": approval.reason}
                if approval_row is None:
                    session.add(ApprovalRecordModel(action_id=approval.action_id, **approval_values))
                else:
                    for name, value in approval_values.items():
                        setattr(approval_row, name, value)
        for outreach in result.outreach_results:
            draft_id = result.outreach_draft_ids.get(outreach.outreach_id)
            draft = next((d for d in result.drafts if d.draft_id == draft_id), None)
            if draft is None:
                continue
            row = await session.get(OutreachRecord, outreach.outreach_id)
            values = {"workflow_id": result.workflow_id, "draft_id": draft.draft_id,
                      "idempotency_key": outreach.idempotency_key, "status": outreach.status.value,
                      "recipient": draft.recipient.email if draft.recipient else "unknown",
                      "provider": outreach.provider_result.provider_name if outreach.provider_result else None,
                      "provider_message_id": outreach.provider_result.provider_message_id if outreach.provider_result else None,
                      "provider_result_json": _json(outreach.provider_result) if outreach.provider_result else None,
                      "sent_at": outreach.provider_result.timestamp if outreach.status.value == "sent" and outreach.provider_result else None}
            if row is None:
                session.add(OutreachRecord(outreach_id=outreach.outreach_id, **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)
            for event in outreach.events:
                event_row = await session.get(OutreachEventRecord, event.event_id)
                event_values = {"outreach_id": outreach.outreach_id, "event_type": event.event_type.value,
                                "occurred_at": event.occurred_at, "provider": event.provider,
                                "provider_event_type": event.provider_event_type,
                                "metadata_json": _safe_details(event.metadata)}
                if event_row is None:
                    session.add(OutreachEventRecord(event_id=event.event_id, **event_values))
                else:
                    for name, value in event_values.items():
                        setattr(event_row, name, value)
        for followup in result.follow_up_results:
            plan = followup.plan
            if plan is None:
                continue
            row = await session.get(FollowUpPlanRecord, plan.plan_id)
            values = {"outreach_id": plan.original_outreach_id,
                      "follow_up_number": plan.follow_up_number, "recipient": plan.recipient,
                      "due_at": plan.due_at, "status": plan.status.value, "reason": plan.reason}
            if row is None:
                session.add(FollowUpPlanRecord(plan_id=plan.plan_id, **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)

    async def _project_tools(self, session: AsyncSession, result: WorkflowResult) -> None:
        step_by_source = {step.logical_item_id: step for step in result.steps
                          if step.step_type.value == "student_crawling"}
        for crawl in result.crawl_results:
            tool_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                     f"{result.workflow_id}:crawl4ai:{crawl.requested_url}"))
            row = await session.get(ToolCallRecord, tool_id)
            step = step_by_source.get(crawl.requested_url)
            values = {"workflow_id": result.workflow_id, "step_id": step.step_id if step else None,
                      "tool_name": "crawl4ai", "tool_version": crawl.provenance.tool_version,
                      "status": "succeeded" if crawl.success else (crawl.error.code.value if crawl.error else "failed"),
                      "called_at": crawl.provenance.crawl_timestamp,
                      "duration_seconds": crawl.provenance.duration_seconds,
                      "metadata_json": {"source_url": crawl.requested_url,
                                        "status_code": crawl.status_code,
                                        "error_code": crawl.error.code.value if crawl.error else None}}
            if row is None:
                session.add(ToolCallRecord(tool_call_id=tool_id, **values))
            else:
                for name, value in values.items():
                    setattr(row, name, value)

    async def _project_metrics(self, session: AsyncSession, result: WorkflowResult) -> None:
        metrics = result.metrics.model_dump(mode="json")
        values: dict[str, float] = {
            **{key: float(value) for key, value in metrics.items()
               if isinstance(value, (int, float))},
            "workflows_started": 1,
            "workflows_completed": float(result.status.value == "completed"),
            "workflows_failed": float(result.status.value == "failed"),
            "final_year_verified": float(sum(item.final_year_status.value == "final_year_verified"
                                              for item in result.validated_profiles)),
            "ai_interest_supported": float(sum(item.ai_interest_status.value == "ai_interest_supported"
                                                for item in result.validated_profiles)),
            "emails_rejected": float(sum(d.status.value == "rejected" for d in result.drafts)),
            "emails_failed": float(sum(item.status.value in {"failed", "temporary_failure", "permanent_failure"}
                                       for item in result.outreach_results)),
        }
        if result.metrics.candidates_validated:
            values["validation_rate"] = result.metrics.candidates_validated / max(result.metrics.candidates_discovered, 1)
        if result.metrics.candidates_discovered:
            values["match_rate"] = result.metrics.candidates_matched / result.metrics.candidates_discovered
        for name, value in values.items():
            metric_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{result.workflow_id}:{name}"))
            row = await session.get(KPIMetricRecord, metric_id)
            if row is None:
                session.add(KPIMetricRecord(metric_id=metric_id, workflow_id=result.workflow_id,
                                            metric_name=name, value=value,
                                            recorded_at=result.updated_at))
            else:
                row.value = value
                row.recorded_at = result.updated_at

    async def load_workflow(self, workflow_id: str) -> tuple[WorkflowRequest, WorkflowResult, int] | None:
        self.ensure_configured()
        async with self.sessions() as session:
            row = await session.get(WorkflowRecord, workflow_id)
            if row is None:
                return None
            request = WorkflowRequest.model_validate(row.request_json)
            result = WorkflowResult.model_validate(row.result_json)
            hop_count = int((row.runtime_json or {}).get("hop_count", 0))
            return request, result, hop_count

    async def list_workflows(self, *, limit: int = 50, offset: int = 0,
                             owner_id: str | None = None, include_all: bool = False) -> list[WorkflowResult]:
        self.ensure_configured()
        async with self.sessions() as session:
            query = select(WorkflowRecord)
            if not include_all:
                query = query.where(WorkflowRecord.owner_id == owner_id)
            rows = (await session.scalars(query.order_by(
                WorkflowRecord.created_at.desc()).limit(min(max(limit, 1), 200)).offset(max(offset, 0)))).all()
            return [WorkflowResult.model_validate(row.result_json) for row in rows]

    async def can_access_workflow(self, workflow_id: str, *, owner_id: str,
                                  is_admin: bool = False) -> bool:
        self.ensure_configured()
        async with self.sessions() as session:
            workflow = await session.get(WorkflowRecord, workflow_id)
            if workflow is None:
                return False
            return is_admin or workflow.owner_id == owner_id

    async def list_steps(self, workflow_id: str) -> list[dict[str, Any]]:
        self.ensure_configured()
        async with self.sessions() as session:
            rows = (await session.scalars(select(WorkflowStepRecord)
                                          .where(WorkflowStepRecord.workflow_id == workflow_id)
                                          .order_by(WorkflowStepRecord.started_at))).all()
            return [{"step_id": row.step_id, "step_type": row.step_type, "state": row.state,
                     "agent_name": row.agent_name, "agent_version": row.agent_version,
                     "started_at": row.started_at.isoformat() if row.started_at else None,
                     "ended_at": row.ended_at.isoformat() if row.ended_at else None,
                     "duration_seconds": float(row.duration_seconds) if row.duration_seconds is not None else None,
                     "retry_count": row.retry_count,
                     "error_category": row.error_category, "error": row.error,
                     "input_metadata": row.input_metadata, "output_metadata": row.output_metadata}
                    for row in rows]

    async def list_audit(self, workflow_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        self.ensure_configured()
        async with self.sessions() as session:
            rows = (await session.scalars(select(AuditLogRecord)
                                          .where(AuditLogRecord.workflow_id == workflow_id)
                                          .order_by(AuditLogRecord.occurred_at)
                                          .limit(min(max(limit, 1), 1000)))).all()
            return [{"audit_id": row.audit_id, "event_type": row.event_type,
                     "occurred_at": row.occurred_at.isoformat(), "step_id": row.step_id,
                     "agent_name": row.agent_name, "agent_version": row.agent_version,
                     "details": row.details_json} for row in rows]

    async def collect_kpis(self) -> dict[str, float]:
        self.ensure_configured()
        async with self.sessions() as session:
            rows = (await session.execute(select(KPIMetricRecord.metric_name,
                                                  func.sum(KPIMetricRecord.value))
                                          .group_by(KPIMetricRecord.metric_name))).all()
            metrics = {name: float(value or 0) for name, value in rows}
            events = (await session.execute(select(AuditLogRecord.event_type,
                                                    func.count(AuditLogRecord.audit_id))
                                            .group_by(AuditLogRecord.event_type))).all()
            event_counts = {name: float(value) for name, value in events}
            metrics.update({
                "emails_approved": event_counts.get("EMAIL_HUMAN_APPROVED", 0),
                "emails_rejected": event_counts.get("EMAIL_HUMAN_REJECTED", 0),
                "emails_sent": event_counts.get("OUTREACH_SEND_SUCCEEDED", 0),
                "emails_failed": event_counts.get("OUTREACH_SEND_FAILED", 0),
                "replies_received": event_counts.get("OUTREACH_REPLY_RECORDED", 0),
                "opt_outs": event_counts.get("OUTREACH_OPT_OUT_RECORDED", 0),
                "follow_ups_planned": event_counts.get("FOLLOW_UP_PLANNED", 0),
                "workflows_started": event_counts.get("WORKFLOW_STARTED", 0),
                "workflows_completed": event_counts.get("WORKFLOW_COMPLETED", 0),
                "workflows_failed": event_counts.get("WORKFLOW_FAILED", 0),
            })
            return metrics


class PostgresEventRecorder:
    """Durable audit recorder with conservative details filtering."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], database_url: str) -> None:
        self.sessions = sessions
        self.database_url = database_url
        self._buffer: ContextVar[list[AgentEvent] | None] = ContextVar(
            f"audit_buffer_{id(self)}", default=None)

    @asynccontextmanager
    async def buffered(self):
        """Batch audit inserts during the explicitly synthetic high-volume demo."""
        existing = self._buffer.get()
        if existing is not None:
            yield
            return
        token = self._buffer.set([])
        try:
            yield
        finally:
            pending = self._buffer.get() or []
            self._buffer.reset(token)
            if pending:
                await self.record_many(pending)

    async def record(self, event: AgentEvent) -> None:
        pending = self._buffer.get()
        if pending is not None:
            pending.append(event)
            return
        await self.record_many([event])

    async def record_many(self, events: list[AgentEvent]) -> None:
        if not events:
            return
        validate_storage_url(self.database_url, allow_sqlite=True)
        async with self.sessions.begin() as session:
            session.add_all([
                AuditLogRecord(
                    audit_id=str(uuid.uuid4()), workflow_id=event.workflow_id,
                    step_id=event.workflow_step_id, execution_id=event.execution_id,
                    event_type=event.event_type.value, agent_name=event.agent_name,
                    agent_version=event.agent_version, occurred_at=event.occurred_at,
                    details_json=_safe_details(event.details))
                for event in events
            ])


def _canonical_for_profile(validated, canonical, profile_index: int) -> str | None:
    if profile_index < 0 or profile_index >= len(validated):
        return None
    urls = {str(item.source_url) for item in validated[profile_index].profile.source_references}
    for student in canonical:
        for profile in student.source_profiles:
            if urls.intersection(str(item.source_url) for item in profile.source_references):
                return student.canonical_id
    return None
