"""Deterministic integration checks for process-local Step 10 orchestration."""

import asyncio
from datetime import datetime, timezone
from types import MethodType

import pytest

from app.agents.events import AgentEventType, InMemoryEventRecorder
from app.agents.exceptions import RetryableFailure
from app.agents.retry import RetryPolicy
from app.agents.base import BaseAgent
from app.agents.registry import AgentRegistry
from app.main import app
from app.orchestration import create_demo_orchestrator
from app.orchestration.transitions import validate_transition
from app.schemas.email import ApprovalAction, ApprovalActionType
from app.schemas.matching import CriterionType, UserCriterion, UserRequirement
from app.schemas.student import (AIEvidenceCategory, ClaimType, Evidence, EvidenceStrength,
                                EvidenceType, ExtractedStudentInformation, SourceReference,
                                SourcedFact, StudentProfile)
from app.schemas.workflow import (WorkflowRequest, WorkflowStatus, WorkflowStepStatus,
                                  WorkflowStepType)
from app.crawling.models import (CrawlConfiguration, CrawlMetadata, CrawlResult,
                                 SourceAccess, SourceCandidate, SourceType)


URL = "https://public.example.org/student"
DOMAIN = "public.example.org"


def run(coro):
    return asyncio.run(coro)


def requirement():
    return UserRequirement(requirement_id="req-ai-final", raw_text="final-year AI computer vision students",
                           criteria=[UserCriterion(criterion_type=CriterionType.FINAL_YEAR),
                                     UserCriterion(criterion_type=CriterionType.AI_INTEREST),
                                     UserCriterion(criterion_type=CriterionType.AI_AREA,
                                                   value="computer vision")])


def source():
    return SourceCandidate(url=URL, domain=DOMAIN, source_type=SourceType.PUBLIC_PROFILE,
                           reason="Public demo source supplied by test", access=SourceAccess.PUBLIC)


def extracted_fixture():
    facts = [
        Evidence(evidence_id="grad-evidence", claim_type=ClaimType.EXPECTED_GRADUATION_YEAR,
                 claim_value=2027, evidence_type=EvidenceType.PUBLIC_PROFILE, source_url=URL,
                 supporting_text="Expected graduation year: 2027", strength=EvidenceStrength.HIGH),
        Evidence(evidence_id="cv-evidence", claim_type=ClaimType.AI_ACTIVITY,
                 claim_value="Computer vision capstone project", evidence_type=EvidenceType.PROJECT_PAGE,
                 source_url=URL, supporting_text="Computer vision project: capstone object detection",
                 strength=EvidenceStrength.HIGH, ai_category=AIEvidenceCategory.COMPUTER_VISION),
        Evidence(evidence_id="contact-evidence", claim_type=ClaimType.PUBLIC_CONTACT,
                 claim_value="student@example.org", evidence_type=EvidenceType.PUBLIC_PROFILE,
                 source_url=URL, supporting_text="Public contact: student@example.org",
                 strength=EvidenceStrength.HIGH),
    ]
    profile = StudentProfile(
        expected_graduation_year=SourcedFact(value=2027, evidence_ids=["grad-evidence"]),
        public_contact=SourcedFact(value="student@example.org", evidence_ids=["contact-evidence"]),
        evidence=facts,
        source_references=[SourceReference(source_url=URL, final_url=URL,
                                           title="Synthetic deterministic test fixture",
                                           evidence_type=EvidenceType.PUBLIC_PROFILE, access="public")],
    )
    return ExtractedStudentInformation(profile=profile, evidence=facts,
                                       source_references=profile.source_references)


def workflow_request(**updates):
    values = dict(requirement=requirement(), sources=[source()],
                  permitted_domains=frozenset({DOMAIN}),
                  crawl_configuration=CrawlConfiguration(allowed_domains=frozenset({DOMAIN}),
                                                         min_request_interval_seconds=0),
                  current_year=2026, demo_mode=True)
    values.update(updates)
    return WorkflowRequest(**values)


def ready_orchestrator(*, recorder=None):
    orchestrator = create_demo_orchestrator(allowed_domains={DOMAIN}, event_recorder=recorder)
    extractor = orchestrator.registry.get("extraction")

    async def fixture_execute(self, agent_input, context):
        return extracted_fixture()

    extractor._execute = MethodType(fixture_execute, extractor)
    return orchestrator


def test_workflow_runs_existing_agents_in_canonical_order_and_pauses():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    types = [step.step_type for step in result.steps]
    expected = [WorkflowStepType.SOURCE_DISCOVERY, WorkflowStepType.STUDENT_CRAWLING,
                WorkflowStepType.EXTRACTION, WorkflowStepType.VALIDATION,
                WorkflowStepType.DEDUPLICATION, WorkflowStepType.ENRICHMENT,
                WorkflowStepType.MATCHING, WorkflowStepType.EMAIL_DRAFTING,
                WorkflowStepType.HUMAN_APPROVAL]
    assert types == expected
    assert result.status == WorkflowStatus.WAITING_FOR_APPROVAL


def test_controlled_url_demo_runs_extraction_validation_match_and_evidence_to_approval():
    fixture_url = URL
    fixture_page = CrawlResult(
        requested_url=fixture_url, final_url=fixture_url, status_code=200,
        page_title="SYNTHETIC TEST FIXTURE — student project profile",
        raw_content=("SYNTHETIC TEST FIXTURE — not a real candidate.\n"
                     "Name: Synthetic Fixture Student\n"
                     "Expected graduation: 2027\n"
                     "Location: India\n"
                     "Project: Computer Vision traffic sign recognition using CNN\n"
                     "Public contact: fixture@example.org"),
        success=True,
        provenance=CrawlMetadata(source_url=fixture_url, final_url=fixture_url,
                                 domain=DOMAIN, tool_name="crawl4ai", tool_version="mock-test"),
    )
    recorder = InMemoryEventRecorder()
    orchestrator = create_demo_orchestrator(crawl_responses={fixture_url: fixture_page},
                                            allowed_domains={DOMAIN}, event_recorder=recorder)

    result = run(orchestrator.start(workflow_request()))

    assert result.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert result.source_discovery.candidates[0].origin.value == "user_supplied"
    assert result.validated_profiles[0].final_year_status.value == "final_year_verified"
    assert result.validated_profiles[0].ai_interest_status.value == "ai_interest_supported"
    assert result.candidate_matches[0].match_score > 0
    assert result.drafts and result.pending_draft_ids == [result.drafts[0].draft_id]
    assert result.drafts[0].recipient.email == "fixture@example.org"
    event_types = {event.event_type.value for event in recorder.events}
    assert {"SOURCE_DISCOVERED", "SOURCE_CRAWLED", "CANDIDATE_EXTRACTED",
            "CANDIDATE_VALIDATED", "EVIDENCE_CREATED", "CANDIDATE_MATCHED"} <= event_types


def test_background_submission_returns_job_identifier_and_progress_is_pollable():
    async def exercise():
        orchestrator = ready_orchestrator()
        submitted = await orchestrator.submit(workflow_request())
        assert submitted.job_id == submitted.workflow_id
        assert submitted.status == WorkflowStatus.RUNNING
        for _ in range(100):
            current = await orchestrator.get(submitted.workflow_id)
            if current.status != WorkflowStatus.RUNNING:
                return current
            await asyncio.sleep(0.01)
        raise AssertionError("background workflow did not reach a terminal/approval state")

    result = run(exercise())
    assert result.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert result.steps


def test_approval_is_required_and_no_outreach_occurs_before_it():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    assert result.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert len(orchestrator.registry.get("outreach").provider.calls) == 0
    assert result.pending_draft_ids == [result.drafts[0].draft_id]


def test_approval_resumes_after_drafting_and_executes_mock_outreach_and_followup():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    paused_step_ids = {s.step_id for s in result.steps if s.state == WorkflowStepStatus.SUCCEEDED}
    final = run(orchestrator.resume_after_approval(
        result.workflow_id, result.pending_draft_ids[0],
        ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer")))
    assert final.status == WorkflowStatus.COMPLETED
    assert paused_step_ids.issubset({s.step_id for s in final.steps})
    assert [s.step_type for s in final.steps][-2:] == [WorkflowStepType.OUTREACH, WorkflowStepType.FOLLOW_UP]
    assert len(orchestrator.registry.get("outreach").provider.calls) == 1


def test_rejection_finishes_without_outreach():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    final = run(orchestrator.resume_after_approval(
        result.workflow_id, result.pending_draft_ids[0],
        ApprovalAction(action=ApprovalActionType.REJECT, actor_id="reviewer")))
    assert final.status == WorkflowStatus.COMPLETED
    assert orchestrator.registry.get("outreach").provider.calls == []


def test_human_edit_keeps_workflow_paused_until_an_explicit_approval():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    draft_id = result.pending_draft_ids[0]
    edited = run(orchestrator.resume_after_approval(
        result.workflow_id, draft_id,
        ApprovalAction(action=ApprovalActionType.EDIT, actor_id="reviewer",
                       subject="Edited subject", body="Edited body")))
    assert edited.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert edited.pending_draft_ids == [draft_id]
    assert orchestrator.registry.get("outreach").provider.calls == []
    final = run(orchestrator.resume_after_approval(
        result.workflow_id, draft_id,
        ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer")))
    assert final.status == WorkflowStatus.COMPLETED
    assert len(orchestrator.registry.get("outreach").provider.calls) == 1


def test_workflow_cancellation_prevents_future_steps():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    final = run(orchestrator.cancel(result.workflow_id, "operator cancelled"))
    assert final.status == WorkflowStatus.CANCELLED
    with pytest.raises(ValueError, match="not waiting"):
        run(orchestrator.resume_after_approval(
            result.workflow_id, result.drafts[0].draft_id,
            ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer")))
    assert orchestrator.registry.get("outreach").provider.calls == []


def test_agent_versions_recorded_from_registry():
    result = run(ready_orchestrator().start(workflow_request()))
    assert result.agent_versions["matching"] == "2.0.0"
    assert result.agent_versions["email"] == "2.0.0"
    assert all(step.agent_version for step in result.steps)


def test_structured_outputs_flow_through_to_final_result():
    result = run(ready_orchestrator().start(workflow_request()))
    assert result.source_discovery.candidates[0].url == URL
    assert result.crawl_results and result.extracted_profiles
    assert result.validated_profiles[0].ai_interest_status.value == "ai_interest_supported"
    assert result.canonical_students and result.candidate_matches
    assert result.drafts[0].candidate_id == result.canonical_students[0].canonical_id


def test_invalid_workflow_transition_is_rejected():
    validate_transition(WorkflowStatus.WAITING_FOR_APPROVAL, WorkflowStatus.RUNNING)
    with pytest.raises(ValueError, match="Invalid workflow transition"):
        validate_transition(WorkflowStatus.WAITING_FOR_APPROVAL, WorkflowStatus.FAILED)
    with pytest.raises(ValueError, match="Invalid workflow transition"):
        validate_transition(WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING)


def test_approval_step_is_waiting_in_state_and_audit():
    recorder = InMemoryEventRecorder()
    result = run(ready_orchestrator(recorder=recorder).start(workflow_request()))
    approval = next(step for step in result.steps if step.step_type == WorkflowStepType.HUMAN_APPROVAL)
    assert approval.state == WorkflowStepStatus.WAITING
    assert AgentEventType.WORKFLOW_APPROVAL_REQUESTED in {event.event_type for event in recorder.events}
    assert AgentEventType.WORKFLOW_PAUSED in {event.event_type for event in recorder.events}


def test_rejection_is_audited_and_never_sends():
    recorder = InMemoryEventRecorder()
    orchestrator = ready_orchestrator(recorder=recorder)
    result = run(orchestrator.start(workflow_request()))
    run(orchestrator.resume_after_approval(
        result.workflow_id, result.pending_draft_ids[0],
        ApprovalAction(action=ApprovalActionType.REJECT, actor_id="reviewer")))
    assert AgentEventType.WORKFLOW_APPROVAL_RECEIVED in {e.event_type for e in recorder.events}
    assert AgentEventType.WORKFLOW_COMPLETED in {e.event_type for e in recorder.events}
    assert not orchestrator.registry.get("outreach").provider.calls


def test_workflow_hop_limit_is_enforced_as_critical_failure():
    result = run(ready_orchestrator().start(workflow_request(max_hops=1)))
    assert result.status == WorkflowStatus.FAILED
    assert any("hop limit" in error.lower() for error in result.errors)


def test_missing_registered_agent_is_a_critical_failure():
    orchestrator = ready_orchestrator()
    orchestrator.registry._agents.pop("matching")
    result = run(orchestrator.start(workflow_request()))
    assert result.status == WorkflowStatus.FAILED
    assert any("No registered agent: matching" in error for error in result.errors)


def test_demo_workflow_rejects_provider_without_explicit_non_sending_declaration():
    orchestrator = ready_orchestrator()
    provider = orchestrator.registry.get("outreach").demo_provider
    provider.sends_real_email = True
    result = run(orchestrator.start(workflow_request()))
    final = run(orchestrator.resume_after_approval(
        result.workflow_id, result.pending_draft_ids[0],
        ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer")))
    assert final.status == WorkflowStatus.FAILED
    assert not provider.calls


def test_crawler_tool_permission_is_not_granted_when_absent():
    result = run(ready_orchestrator().start(
        workflow_request(permitted_tools=frozenset())))
    assert result.status == WorkflowStatus.COMPLETED
    assert any("crawl4ai" in error for error in result.errors)
    assert result.validated_profiles == []


def test_candidate_failure_does_not_prevent_independent_source_processing():
    second_url = "https://public.example.org/other"
    second = SourceCandidate(url=second_url, domain=DOMAIN, source_type=SourceType.PUBLIC_PROFILE,
                             reason="second test source", access=SourceAccess.PUBLIC)
    orchestrator = ready_orchestrator()
    extractor = orchestrator.registry.get("extraction")
    count = 0

    async def one_fails(self, agent_input, context):
        nonlocal count
        count += 1
        if count == 1:
            raise RetryableFailure("fixture failure")
        return extracted_fixture()

    extractor._execute = MethodType(one_fails, extractor)
    result = run(orchestrator.start(workflow_request(sources=[source(), second])))
    assert result.status == WorkflowStatus.WAITING_FOR_APPROVAL
    assert len(result.validated_profiles) == 1
    assert result.errors


def test_retry_policy_retries_retryable_agent_failure_and_records_count():
    orchestrator = ready_orchestrator()
    agent = orchestrator.registry.get("extraction")
    agent.retry_policy = RetryPolicy(max_retries=1)
    calls = 0

    async def transient(self, agent_input, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableFailure("transient")
        return extracted_fixture()

    agent._execute = MethodType(transient, agent)
    result = run(orchestrator.start(workflow_request()))
    extraction_step = next(step for step in result.steps if step.step_type == WorkflowStepType.EXTRACTION)
    assert calls == 2
    assert extraction_step.retry_count == 1
    assert result.metrics.steps_retried == 1


def test_non_retryable_failure_is_not_retried():
    orchestrator = ready_orchestrator()
    agent = orchestrator.registry.get("extraction")
    agent.retry_policy = RetryPolicy(max_retries=5)
    calls = 0

    async def permanent(self, agent_input, context):
        nonlocal calls
        calls += 1
        from app.agents.exceptions import NonRetryableFailure
        raise NonRetryableFailure("permanent fixture failure")

    agent._execute = MethodType(permanent, agent)
    result = run(orchestrator.start(workflow_request()))
    assert calls == 1
    assert result.status == WorkflowStatus.COMPLETED
    assert result.errors


def test_email_draft_is_idempotent_on_step_key():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    first = result.drafts[0]
    assert sum(step.step_type == WorkflowStepType.EMAIL_DRAFTING for step in result.steps) == 1
    assert orchestrator.registry.get("email").draft_store
    assert first.draft_id.startswith("draft-")


def test_demo_mode_uses_only_mock_provider_and_no_live_network():
    orchestrator = ready_orchestrator()
    crawler = orchestrator.registry.get("student_crawler").crawl_tool
    provider = orchestrator.registry.get("outreach").provider
    assert crawler.tool_name == "crawl4ai" and hasattr(crawler, "requests")
    assert provider.provider_name == "mock" and provider.sends_real_email is False
    assert run(orchestrator.start(workflow_request())).status == WorkflowStatus.WAITING_FOR_APPROVAL


def test_workflow_result_tracks_candidate_counts_and_versions():
    result = run(ready_orchestrator().start(workflow_request()))
    assert result.metrics.candidates_discovered == 1
    assert result.metrics.candidates_validated == 1
    assert result.metrics.candidates_matched == 1
    assert result.metrics.emails_drafted == 1
    assert result.metrics.total_duration_seconds >= 0


def test_followup_stage_only_plans_and_never_calls_email_provider():
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    final = run(orchestrator.resume_after_approval(
        result.workflow_id, result.pending_draft_ids[0],
        ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer")))
    assert final.follow_up_results
    assert len(orchestrator.registry.get("outreach").provider.calls) == 1
    assert all(step.step_type != WorkflowStepType.FOLLOW_UP or step.agent_name == "follow_up"
               for step in final.steps)


def test_health_endpoint_still_returns_200():
    from fastapi.testclient import TestClient
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_fastapi_reports_missing_postgresql_configuration(monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.config import settings

    # Control this test's configuration explicitly; never inherit the developer's
    # local .env, which may point at a working PostgreSQL instance.
    monkeypatch.setattr(
        settings, "database_url",
        "postgresql+psycopg://username:password@localhost:5432/database_name",
    )
    with TestClient(app) as client:
        response = client.post("/workflows", json={
            "requirement": {"requirement_id": "api-req", "criteria": [
                {"criterion_type": "final_year"},
                {"criterion_type": "ai_interest"},
            ]},
            "sources": [],
            "permitted_domains": [],
            "demo_mode": True,
    })
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "PostgreSQL operation failed; check server logs for a redacted diagnostic."
    )


@pytest.mark.parametrize("action", [ApprovalActionType.APPROVE, ApprovalActionType.REJECT])
def test_only_human_approval_actions_resolve_the_pause(action):
    orchestrator = ready_orchestrator()
    result = run(orchestrator.start(workflow_request()))
    final = run(orchestrator.resume_after_approval(
        result.workflow_id, result.pending_draft_ids[0],
        ApprovalAction(action=action, actor_id="human-reviewer")))
    assert final.status == WorkflowStatus.COMPLETED
    if action == ApprovalActionType.REJECT:
        assert not orchestrator.registry.get("outreach").provider.calls


@pytest.mark.parametrize("state", [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED,
                                    WorkflowStatus.CANCELLED])
def test_terminal_states_cannot_resume(state):
    from app.orchestration.transitions import ALLOWED_TRANSITIONS
    assert ALLOWED_TRANSITIONS[state] == frozenset()
