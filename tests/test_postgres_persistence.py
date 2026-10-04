"""Restart and storage integration checks; PostgreSQL is opt-in and reported separately."""

import asyncio
import hashlib
import importlib.util
import os
from types import MethodType
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.persistence import (AgentRunRecord, ApprovalRecordModel, AuditLogRecord,
                                DeduplicationRecord,
                                EmailDraftRecord, EmailDraftVersionRecord, KPIMetricRecord,
                                MatchRecord, OptOutRecordModel, OutreachEventRecord,
                                OutreachRecord, WorkflowRecord,
                                WorkflowStepRecord)
from app.models import (StudentEvidenceRecord, StudentRecord, StudentSourceRecord,
                        FollowUpPlanRecord, ReplyRecordModel)
from app.orchestration import create_demo_orchestrator
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.schemas.email import ApprovalAction, ApprovalActionType
from app.schemas.outreach import (OutreachRequest, OutreachStatus, OptOutRequest,
                                  FollowUpRequest, FollowUpStatus)
from app.schemas.student import (ClaimType, Evidence, EvidenceStrength, EvidenceType,
                                ExtractedStudentInformation, SourceReference, SourcedFact)
from app.agents.events import AgentEventType
from app.agents.implementations.follow_up import OutreachEventService
from app.agents.implementations.outreach import (EMAIL_PROVIDER_TOOL, OPT_OUT_TOOL,
                                                OUTREACH_STORE_TOOL, RATE_LIMITER_TOOL)
from app.schemas.workflow import WorkflowStatus
from tests.test_orchestrator import DOMAIN, extracted_fixture, workflow_request


class _RedactedDatabaseURL(str):
    """Keep pytest's local-variable diagnostics from rendering URL credentials."""

    def __repr__(self) -> str:
        try:
            return repr(make_url(str(self)).render_as_string(hide_password=True))
        except Exception:
            return "<redacted database URL>"


def _postgres_database_url() -> _RedactedDatabaseURL:
    from app.core.config import get_settings
    from app.db.persistence import validate_database_url

    configured_url = os.getenv("TEST_DATABASE_URL") or get_settings().database_url
    return _RedactedDatabaseURL(validate_database_url(configured_url))


def _postgres_integration_is_configured() -> bool:
    from app.db.persistence import DatabaseConfigurationError

    try:
        _postgres_database_url()
    except DatabaseConfigurationError:
        return False
    return True


def _safe_workflow_diagnostics(result, database_url: str) -> str:
    parsed = make_url(str(database_url))
    raw_url = parsed.render_as_string(hide_password=False)
    safe_url = parsed.render_as_string(hide_password=True)
    details = [f"status={result.status.value}", "errors:"]
    details.extend(result.errors)
    details.append("failed_steps:")
    details.extend(
        f"{step.step_type.value}/{step.agent_name}: {step.error}"
        for step in result.steps if step.error
    )
    rendered = "\n".join(details).replace(raw_url, safe_url)
    if parsed.password:
        rendered = rendered.replace(parsed.password, "[REDACTED]")
    return rendered


def _configure_extractor(orchestrator, *, source_aware: bool = False):
    extractor = orchestrator.registry.get("extraction")

    async def fixture_execute(self, agent_input, context):
        if not source_aware:
            return extracted_fixture()
        extracted = extracted_fixture()
        source_url = str(agent_input.payload.requested_url)
        suffix = hashlib.sha256(source_url.encode()).hexdigest()[:10]
        evidence_ids = {item.evidence_id: f"{suffix}-{item.evidence_id}"
                        for item in extracted.evidence}
        evidence = [item.model_copy(update={
            "evidence_id": evidence_ids[item.evidence_id],
            "source_url": source_url,
            "final_url": source_url,
        }, deep=True) for item in extracted.evidence]
        name_id, university_id = f"{suffix}-name", f"{suffix}-university"
        evidence.extend([
            Evidence(evidence_id=name_id, claim_type=ClaimType.NAME,
                     claim_value="Demo Student", evidence_type=EvidenceType.PUBLIC_PROFILE,
                     source_url=source_url, supporting_text="Name: Demo Student",
                     strength=EvidenceStrength.HIGH),
            Evidence(evidence_id=university_id, claim_type=ClaimType.UNIVERSITY,
                     claim_value="Demo Institute", evidence_type=EvidenceType.PUBLIC_PROFILE,
                     source_url=source_url, supporting_text="University: Demo Institute",
                     strength=EvidenceStrength.HIGH),
        ])
        profile = extracted.profile.model_copy(deep=True)
        profile.name = SourcedFact(value="Demo Student", evidence_ids=[name_id])
        profile.university = SourcedFact(value="Demo Institute", evidence_ids=[university_id])
        profile.expected_graduation_year = SourcedFact(
            value=2027, evidence_ids=[evidence_ids["grad-evidence"]])
        profile.public_contact = SourcedFact(
            value="student@example.org", evidence_ids=[evidence_ids["contact-evidence"]])
        profile.evidence = evidence
        references = [SourceReference(source_url=source_url, final_url=source_url,
                                      title="Synthetic deterministic test fixture",
                                      evidence_type=EvidenceType.PUBLIC_PROFILE, access="public")]
        profile.source_references = references
        return ExtractedStudentInformation(profile=profile, evidence=evidence,
                                           source_references=references)

    extractor._execute = MethodType(fixture_execute, extractor)


def _test_url(url: str) -> _RedactedDatabaseURL:
    if url.startswith("sqlite+aiosqlite:///"):
        return _RedactedDatabaseURL(url)
    from app.db.persistence import validate_database_url
    return _RedactedDatabaseURL(validate_database_url(url))


async def _exercise_postgres_in_isolated_schema(database_url: _RedactedDatabaseURL) -> None:
    """Run against a temporary schema so hdl_db and its existing tables are preserved."""
    parsed_url = make_url(str(database_url))
    schema = f"step11_test_{uuid4().hex[:12]}"
    root_engine = create_async_engine(database_url)
    created = False
    try:
        async with root_engine.begin() as connection:
            from sqlalchemy import text
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        query = dict(parsed_url.query)
        query["options"] = f"-csearch_path={schema}"
        isolated_url = _RedactedDatabaseURL(
            parsed_url.set(query=query).render_as_string(hide_password=False))
        await _exercise_restart(isolated_url, create_schema=True,
                                 include_duplicate_source=True)

        # Assert the normalized projection categories not already queried above.
        isolated_engine = create_async_engine(isolated_url)
        try:
            async with isolated_engine.connect() as connection:
                workflow_id = (await connection.execute(
                    select(WorkflowRecord.workflow_id).limit(1))).scalar_one()
                entity_types = [
                    WorkflowStepRecord, AgentRunRecord, StudentRecord, StudentSourceRecord,
                    StudentEvidenceRecord, MatchRecord, EmailDraftRecord,
                    EmailDraftVersionRecord, ApprovalRecordModel, OutreachRecord,
                    OutreachEventRecord, ReplyRecordModel, OptOutRecordModel,
                    DeduplicationRecord, FollowUpPlanRecord, AuditLogRecord, KPIMetricRecord,
                ]
                for entity in entity_types:
                    query = select(entity).where(entity.workflow_id == workflow_id) \
                        if "workflow_id" in entity.__table__.columns else select(entity)
                    assert (await connection.execute(query.limit(1))).first() is not None, entity.__name__
        finally:
            await isolated_engine.dispose()
    finally:
        if created:
            from sqlalchemy import text
            async with root_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await root_engine.dispose()


async def _exercise_restart(database_url: str, *, monkeypatch=None,
                            create_schema: bool = True,
                            include_duplicate_source: bool = False) -> None:
    if database_url.startswith("sqlite+aiosqlite:///"):
        if monkeypatch is None:
            raise AssertionError("SQLite adapter tests must remain explicitly labelled and isolated")
        monkeypatch.setattr("app.db.persistence.validate_database_url", lambda url: url)
        monkeypatch.setattr("app.outreach.persistence.validate_database_url", lambda url: url)
    engine = create_async_engine(database_url)
    try:
        if create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        first = create_demo_orchestrator(sessions=sessions, database_url=database_url,
                                         allowed_domains={DOMAIN})
        _configure_extractor(first, source_aware=include_duplicate_source)
        request = workflow_request()
        if include_duplicate_source:
            second_source = request.sources[0].model_copy(
                update={"url": "https://public.example.org/student-mirror"}, deep=True)
            request = request.model_copy(update={
                "sources": [request.sources[0], second_source]}, deep=True)
        waiting = await first.start(request)
        assert waiting.status == WorkflowStatus.WAITING_FOR_APPROVAL, _safe_workflow_diagnostics(
            waiting, database_url)
        workflow_id, draft_id = waiting.workflow_id, waiting.pending_draft_ids[0]

        # Reconstruct every process-local component while retaining only the DB.
        await engine.dispose()
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        second = create_demo_orchestrator(sessions=sessions, database_url=database_url,
                                          allowed_domains={DOMAIN})
        restored = await second.get(workflow_id)
        assert restored.status == WorkflowStatus.WAITING_FOR_APPROVAL
        complete = await second.resume_after_approval(
            workflow_id, draft_id,
            ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="database-test-reviewer"))
        assert complete.status == WorkflowStatus.COMPLETED
        assert complete.outreach_results[0].status == OutreachStatus.SENT
        assert len(second.registry.get("outreach").provider.calls) == 1

        # A new orchestrator returns the committed send result instead of sending twice.
        third = create_demo_orchestrator(sessions=sessions, database_url=database_url,
                                         allowed_domains={DOMAIN})
        durable = await third.get(workflow_id)
        assert durable.agent_versions["matching"] == "2.0.0"
        assert durable.follow_up_results
        outreach_id = durable.outreach_results[0].outreach_id
        stored_plan = await third.registry.get("follow_up").outreach_store.get_follow_up(outreach_id, 1)
        assert stored_plan is not None
        assert stored_plan.status in {FollowUpStatus.DUE, FollowUpStatus.NOT_DUE}
        duplicate_context = AgentContext(
            workflow_id=workflow_id, permitted_tools=frozenset({
                EMAIL_PROVIDER_TOOL, OPT_OUT_TOOL, RATE_LIMITER_TOOL, OUTREACH_STORE_TOOL}),
            idempotency_key="restart-duplicate-check")
        duplicate = await third.registry.get("outreach").execute(
            AgentInput(payload=OutreachRequest(draft=durable.drafts[0], campaign_id=None)),
            duplicate_context)
        assert duplicate.result.status == OutreachStatus.DUPLICATE_SEND
        assert third.registry.get("outreach").provider.calls == []

        # Reply/opt-out state and follow-up cancellation persist independently of the workflow object.
        event_service = OutreachEventService(third.registry.get("outreach").outreach_store,
                                             event_recorder=third.event_recorder)
        await event_service.record_reply(outreach_id)
        cancelled_plan = await third.registry.get("follow_up").outreach_store.get_follow_up(outreach_id, 1)
        assert cancelled_plan.status == FollowUpStatus.CANCELLED
        opt_out, created = await third.registry.get("outreach").opt_out_service.record_opt_out(
            OptOutRequest(recipient="student@example.org", source="database-restart-test"))
        assert created and opt_out.opted_out

        fourth = create_demo_orchestrator(sessions=sessions, database_url=database_url,
                                          allowed_domains={DOMAIN})
        assert await fourth.registry.get("outreach").opt_out_service.has_opted_out("STUDENT@example.org")
        opt_out_context = AgentContext(
            workflow_id=workflow_id,
            permitted_tools=frozenset({EMAIL_PROVIDER_TOOL, OPT_OUT_TOOL, RATE_LIMITER_TOOL,
                                      OUTREACH_STORE_TOOL, "outreach_event_store"}),
            idempotency_key="post-opt-out-attempt")
        blocked = await fourth.registry.get("outreach").execute(
            AgentInput(payload=OutreachRequest(draft=durable.drafts[0],
                                               campaign_id="post-opt-out-test")),
            opt_out_context)
        assert blocked.result.status == OutreachStatus.OPTED_OUT
        assert fourth.registry.get("outreach").provider.calls == []
        stopped_follow_up = await fourth.registry.get("follow_up").execute(
            AgentInput(payload=FollowUpRequest(outreach_id=blocked.result.outreach_id)),
            opt_out_context)
        assert stopped_follow_up.result.plan is not None
        assert stopped_follow_up.result.plan.status == FollowUpStatus.CANCELLED
        async with sessions() as session:
            assert await session.get(WorkflowRecord, workflow_id) is not None
            assert await session.scalar(select(AgentRunRecord).where(
                AgentRunRecord.workflow_id == workflow_id)) is not None
            assert await session.scalar(select(WorkflowStepRecord).where(
                WorkflowStepRecord.workflow_id == workflow_id)) is not None
            student = await session.scalar(select(StudentRecord).where(
                StudentRecord.workflow_id == workflow_id))
            assert student is not None
            assert await session.scalar(select(StudentSourceRecord).where(
                StudentSourceRecord.student_id == student.student_id)) is not None
            assert await session.scalar(select(StudentEvidenceRecord)) is not None
            assert await session.scalar(select(MatchRecord).where(
                MatchRecord.workflow_id == workflow_id)) is not None
            if include_duplicate_source:
                assert await session.scalar(select(DeduplicationRecord).where(
                    DeduplicationRecord.workflow_id == workflow_id)) is not None
            assert await session.scalar(select(EmailDraftRecord).where(
                EmailDraftRecord.draft_id == draft_id)) is not None
            assert await session.scalar(select(EmailDraftVersionRecord).where(
                EmailDraftVersionRecord.draft_id == draft_id)) is not None
            assert await session.scalar(select(ApprovalRecordModel).where(
                ApprovalRecordModel.draft_id == draft_id)) is not None
            assert await session.scalar(select(OutreachRecord).where(
                OutreachRecord.outreach_id == outreach_id)) is not None
            assert await session.scalar(select(OutreachEventRecord).where(
                OutreachEventRecord.outreach_id == outreach_id)) is not None
            assert await session.scalar(select(ReplyRecordModel).where(
                ReplyRecordModel.outreach_id == outreach_id)) is not None
            assert await session.get(OptOutRecordModel, "student@example.org") is not None
            assert await session.scalar(select(FollowUpPlanRecord).where(
                FollowUpPlanRecord.outreach_id == outreach_id)) is not None
            assert await session.scalar(select(AuditLogRecord).where(
                AuditLogRecord.workflow_id == workflow_id)) is not None
            assert await session.scalar(select(KPIMetricRecord).where(
                KPIMetricRecord.workflow_id == workflow_id)) is not None
            audit_types = set((await session.scalars(select(AuditLogRecord.event_type).where(
                AuditLogRecord.workflow_id == workflow_id))).all())
            global_audit_types = set((await session.scalars(
                select(AuditLogRecord.event_type).where(AuditLogRecord.workflow_id.is_(None))
            )).all())
            assert {
                AgentEventType.WORKFLOW_CREATED.value,
                AgentEventType.WORKFLOW_STARTED.value,
                AgentEventType.WORKFLOW_PAUSED.value,
                AgentEventType.WORKFLOW_APPROVAL_RECEIVED.value,
                AgentEventType.WORKFLOW_RESUMED.value,
                AgentEventType.OUTREACH_SEND_SUCCEEDED.value,
                AgentEventType.FOLLOW_UP_PLANNED.value,
                AgentEventType.WORKFLOW_COMPLETED.value,
            } <= audit_types
            assert {
                AgentEventType.OUTREACH_REPLY_RECORDED.value,
                AgentEventType.OUTREACH_OPT_OUT_RECORDED.value,
            } <= global_audit_types
            kpis = (await session.scalars(select(KPIMetricRecord).where(
                KPIMetricRecord.workflow_id == workflow_id))).all()
            kpi_values = {metric.metric_name: float(metric.value) for metric in kpis}
            assert kpi_values["workflows_started"] == 1
            assert kpi_values["workflows_completed"] == 1
            assert kpi_values["emails_sent"] == 1
            assert kpi_values["follow_ups_planned"] == 1
        audits = await third.persistence.list_audit(workflow_id)
        assert audits
        kpi_aggregates = await third.persistence.collect_kpis()
        assert kpi_aggregates["workflows_started"] == 1
        assert kpi_aggregates["workflows_completed"] == 1
        assert kpi_aggregates["emails_sent"] == 1
        assert kpi_aggregates["replies_received"] == 1
        assert kpi_aggregates["opt_outs"] == 1
    finally:
        await engine.dispose()


@pytest.mark.skipif(not importlib.util.find_spec("aiosqlite"),
                    reason="optional aiosqlite test driver is not installed")
def test_process_restart_and_relational_state_with_sqlite_test_adapter(tmp_path, monkeypatch):
    """SQLite exercises repository behavior only; it is not PostgreSQL verification."""
    database_url = "sqlite+aiosqlite:///" + (tmp_path / "workflow_test.sqlite3").as_posix()
    asyncio.run(_exercise_restart(database_url, monkeypatch=monkeypatch))


@pytest.mark.skipif(not _postgres_integration_is_configured(),
                    reason="configure a valid DATABASE_URL or TEST_DATABASE_URL for PostgreSQL integration")
def test_process_restart_and_relational_state_with_postgresql():
    database_url = _postgres_database_url()
    if os.name == "nt" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_exercise_postgres_in_isolated_schema(database_url))
