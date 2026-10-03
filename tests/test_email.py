import asyncio

import pytest

from app.agents.context import AgentContext
from app.agents.events import AgentEventType, InMemoryEventRecorder
from app.agents.exceptions import ToolPermissionDenied
from app.agents.implementations.email import (
    EmailAgent, EmailApprovalService, InMemoryEmailDraftStore,
)
from app.agents.models import AgentInput
from app.schemas.canonical import CanonicalStudent, CanonicalConflict
from app.schemas.email import (
    ApprovalAction, ApprovalActionType, EmailApprovalRequest, EmailDraftRequest,
    EmailDraftStatus, EmailRecipient,
)
from app.schemas.matching import (
    CandidateMatch, CriterionAssessment, CriterionStatus, CriterionType, MatchStatus,
    UserCriterion, UserRequirement,
)
from app.schemas.student import (
    AIInterestStatus, AIEvidenceCategory, ClaimType, Evidence, EvidenceStrength,
    EvidenceType, FinalYearStatus, SourceReference, SourcedFact, StudentProfile,
)


def run(coro):
    return asyncio.run(coro)


def make_request(*, include_ai=True, include_contact=True, match_status=MatchStatus.MATCH,
                 ai_match=CriterionStatus.MATCHED, final_status=FinalYearStatus.FINAL_YEAR_VERIFIED,
                 conflicting=False, request_id=None):
    url = "https://public.example/student"
    evidence = []

    def add(claim_type, value, *, category=None, strength=EvidenceStrength.HIGH,
            kind=EvidenceType.PUBLIC_PROFILE):
        eid = f"ev-{len(evidence)}"
        evidence.append(Evidence(
            evidence_id=eid, claim_type=claim_type, claim_value=value,
            evidence_type=kind, source_url=url, supporting_text=str(value),
            strength=strength, ai_category=category,
        ))
        return SourcedFact(value=value, evidence_ids=[eid]), evidence[-1]

    name, name_evidence = add(ClaimType.NAME, "Alex Student")
    graduation, graduation_evidence = add(ClaimType.EXPECTED_GRADUATION_YEAR, 2027)
    python_skill, python_evidence = add(ClaimType.SKILL, "Python")
    skills = [python_skill]
    skill_evidences = [python_evidence]
    if include_ai:
        pytorch_skill, pytorch_evidence = add(ClaimType.SKILL, "PyTorch")
        skills.append(pytorch_skill)
        skill_evidences.append(pytorch_evidence)
        activity, ai_evidence = add(ClaimType.AI_ACTIVITY, "Computer Vision object detection project",
                                    category=AIEvidenceCategory.COMPUTER_VISION,
                                    kind=EvidenceType.PROJECT_PAGE)
        project, project_evidence = add(ClaimType.PROJECT, activity.value,
                                        category=AIEvidenceCategory.COMPUTER_VISION,
                                        kind=EvidenceType.PROJECT_PAGE)
        activities = [activity]
        projects = [project]
        all_ai_evidence = [ai_evidence, project_evidence]
        ai_status = AIInterestStatus.AI_INTEREST_SUPPORTED
    else:
        activities, projects, all_ai_evidence = [], [], []
        ai_status = AIInterestStatus.AI_INTEREST_NOT_FOUND
    contact = None
    contact_evidence = None
    if include_contact:
        contact, contact_evidence = add(ClaimType.PUBLIC_CONTACT, "alex@example.org",
                                        kind=EvidenceType.PUBLIC_PROFILE)

    profile = StudentProfile(
        name=name, expected_graduation_year=graduation, skills=skills, projects=projects,
        public_contact=contact, final_year_status=final_status,
        ai_interest_status=ai_status, evidence=evidence,
        source_references=[SourceReference(source_url=url, title="Public student profile", access="public")],
    )
    canonical = CanonicalStudent(
        canonical_id="candidate-1", profile=profile, source_profiles=[profile],
        source_profile_references=[url],
        conflicts=[CanonicalConflict(field="graduation_year", values=[2026, 2027],
                                     evidence_ids=[graduation_evidence.evidence_id],
                                     explanation="Unresolved graduation conflict")] if conflicting else [],
    )
    assessments = [CriterionAssessment(
        criterion_type=CriterionType.FINAL_YEAR, value=None, required=True,
        status=(CriterionStatus.INSUFFICIENT_EVIDENCE if conflicting else
                CriterionStatus.MATCHED if final_status == FinalYearStatus.FINAL_YEAR_VERIFIED
                else CriterionStatus.INSUFFICIENT_EVIDENCE),
        explanation="final year", evidence=[graduation_evidence], source_urls=[url],
    ), CriterionAssessment(
        criterion_type=CriterionType.AI_INTEREST, value=None, required=True,
        status=ai_match if include_ai else CriterionStatus.INSUFFICIENT_EVIDENCE,
        explanation="AI interest", evidence=all_ai_evidence, source_urls=[url] if all_ai_evidence else [],
    ), CriterionAssessment(
        criterion_type=CriterionType.SKILL, value="Python", required=True,
        status=CriterionStatus.MATCHED, explanation="Python skill", evidence=[python_evidence], source_urls=[url],
    )]
    if include_ai:
        assessments.extend([
            CriterionAssessment(criterion_type=CriterionType.AI_AREA, value="Computer Vision", required=False,
                                status=CriterionStatus.MATCHED, explanation="CV", evidence=[all_ai_evidence[0]], source_urls=[url]),
            CriterionAssessment(criterion_type=CriterionType.SKILL, value="PyTorch", required=False,
                                status=CriterionStatus.MATCHED, explanation="PyTorch", evidence=[skill_evidences[-1]], source_urls=[url]),
        ])
    else:
        assessments.append(CriterionAssessment(
            criterion_type=CriterionType.AI_AREA, value="Computer Vision", required=True,
            status=CriterionStatus.INSUFFICIENT_EVIDENCE,
            explanation="No CV evidence", evidence=[], source_urls=[],
        ))
    match = CandidateMatch(
        student_reference="candidate-1", canonical_student_id="candidate-1",
        candidate_name="Alex Student", status=match_status, assessments=assessments,
        explanation="Deterministic test match", conflicts=canonical.conflicts,
    )
    requirement = UserRequirement(criteria=[
        UserCriterion(criterion_type=CriterionType.FINAL_YEAR),
        UserCriterion(criterion_type=CriterionType.AI_INTEREST),
        UserCriterion(criterion_type=CriterionType.SKILL, value="Python"),
        UserCriterion(criterion_type=CriterionType.AI_AREA, value="Computer Vision"),
    ])
    recipient = EmailRecipient(email="alex@example.org", evidence_id=contact_evidence.evidence_id) if contact_evidence else None
    return EmailDraftRequest(request_id=request_id, requirement=requirement, candidate=canonical,
                             match=match, recipient=recipient, signature="Research Team")


def make_draft(*, include_ai=True, include_contact=True, match_status=MatchStatus.MATCH,
               ai_match=CriterionStatus.MATCHED, final_status=FinalYearStatus.FINAL_YEAR_VERIFIED,
               conflicting=False, store=None, recorder=None, request_id=None):
    agent = EmailAgent(draft_store=store, event_recorder=recorder)
    result = run(agent.execute(
        AgentInput[EmailDraftRequest](payload=make_request(
            include_ai=include_ai, include_contact=include_contact, match_status=match_status,
            ai_match=ai_match, final_status=final_status, conflicting=conflicting,
            request_id=request_id,
        )), AgentContext()))
    return agent, result


def test_valid_match_creates_professional_personalized_draft_pending_review():
    _, result = make_draft()
    assert result.success
    draft = result.result.draft
    assert draft.status == EmailDraftStatus.PENDING_REVIEW
    assert draft.subject and draft.greeting == "Hello,"
    assert draft.recipient.source_access == "public"
    assert draft.recipient.source_url == "https://public.example/student"
    assert "Computer Vision object detection project" in draft.body
    assert draft.versions[0].authored_by == "email-agent"
    assert "Would you be available" in draft.body


def test_computer_vision_personalization_keeps_evidence_and_source():
    _, result = make_draft()
    draft = result.result.draft
    point = next(item for item in draft.personalization_points if item.criterion_type == "ai_area")
    ref = point.evidence_references[0]
    assert ref.evidence_id == "ev-4"
    assert ref.source_url == "https://public.example/student"
    assert "ev-4" in {item.evidence_id for item in draft.evidence_references}


def test_cse_or_python_without_ai_evidence_does_not_claim_ai_interest():
    _, result = make_draft(include_ai=False, ai_match=CriterionStatus.INSUFFICIENT_EVIDENCE,
                           match_status=MatchStatus.INSUFFICIENT_EVIDENCE)
    draft = result.result.draft
    assert "AI-related work" not in draft.body
    assert all(point.criterion_type != "ai_interest" for point in draft.personalization_points)
    assert "Python" in draft.body


def test_verified_final_year_can_be_referenced_with_source_evidence():
    _, result = make_draft()
    point = next(item for item in result.result.draft.personalization_points if item.criterion_type == "final_year")
    assert "2027" in point.statement
    assert "expected graduation year" in point.statement
    assert point.evidence_references[0].evidence_id == "ev-1"


def test_partial_or_insufficient_criterion_is_not_presented_as_fact():
    _, result = make_draft(ai_match=CriterionStatus.INSUFFICIENT_EVIDENCE,
                           match_status=MatchStatus.PARTIAL_MATCH)
    draft = result.result.draft
    assert "AI-related work" not in draft.body
    assert all(ref.criterion_type != "ai_interest" for ref in draft.evidence_references)


def test_conflicting_final_year_is_not_presented_as_resolved_fact():
    _, result = make_draft(conflicting=True, final_status=FinalYearStatus.CONFLICTING_EVIDENCE,
                           match_status=MatchStatus.INSUFFICIENT_EVIDENCE)
    draft = result.result.draft
    assert "2027" not in draft.body
    assert all(point.criterion_type != "final_year" for point in draft.personalization_points)


def test_missing_authorized_recipient_returns_structured_blocked_failure():
    _, result = make_draft(include_contact=False)
    assert not result.success
    assert result.result.draft.status == EmailDraftStatus.BLOCKED
    assert "No authorized recipient email" in result.result.draft.blocked_reason


def test_human_approval_preserves_content_and_does_not_send():
    store = InMemoryEmailDraftStore()
    recorder = InMemoryEventRecorder()
    _, result = make_draft(store=store, recorder=recorder)
    original = result.result.draft
    approved = run(EmailApprovalService(store, event_recorder=recorder).apply(
        EmailApprovalRequest(draft_id=original.draft_id,
                             action=ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer-1"))))
    assert approved.status == EmailDraftStatus.APPROVED
    assert approved.approved_subject == original.subject
    assert approved.approved_body == original.body
    assert not hasattr(EmailAgent, "send")


def test_human_rejection_records_reason_and_never_sends():
    store = InMemoryEmailDraftStore()
    _, result = make_draft(store=store)
    rejected = run(EmailApprovalService(store).apply(
        EmailApprovalRequest(draft_id=result.result.draft.draft_id,
                             action=ApprovalAction(action=ApprovalActionType.REJECT,
                                                   actor_id="reviewer-2", reason="Not appropriate"))))
    assert rejected.status == EmailDraftStatus.REJECTED
    assert rejected.approvals[-1].reason == "Not appropriate"


def test_human_edit_preserves_generated_version_and_appends_history():
    store = InMemoryEmailDraftStore()
    _, result = make_draft(store=store)
    original = result.result.draft
    edited = run(EmailApprovalService(store).apply(
        EmailApprovalRequest(draft_id=original.draft_id,
                             action=ApprovalAction(action=ApprovalActionType.EDIT,
                                                   actor_id="reviewer-3", subject="Edited subject",
                                                   body="Edited body"))))
    assert edited.status == EmailDraftStatus.EDITED
    assert edited.generated_subject == original.subject
    assert edited.versions[0].body == original.body
    assert edited.versions[1].body == "Edited body"
    assert edited.approvals[-1].action == ApprovalActionType.EDIT


def test_multiple_edits_keep_ordered_versions_and_can_then_be_approved():
    store = InMemoryEmailDraftStore()
    _, result = make_draft(store=store)
    draft_id = result.result.draft.draft_id
    service = EmailApprovalService(store)
    for index in range(2):
        draft = run(service.apply(EmailApprovalRequest(
            draft_id=draft_id,
            action=ApprovalAction(action=ApprovalActionType.EDIT, actor_id="reviewer",
                                  subject=f"Subject {index + 1}", body=f"Body {index + 1}"),
        )))
    assert [version.version_number for version in draft.versions] == [1, 2, 3]
    assert [version.body for version in draft.versions] == [result.result.draft.body, "Body 1", "Body 2"]
    approved = run(service.apply(EmailApprovalRequest(
        draft_id=draft_id, action=ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer"))))
    assert approved.status == EmailDraftStatus.APPROVED
    assert approved.approved_body == "Body 2"


def test_repeated_identical_request_returns_existing_draft():
    store = InMemoryEmailDraftStore()
    request = make_request(request_id="same-request")
    agent = EmailAgent(draft_store=store)
    first = run(agent.execute(AgentInput[EmailDraftRequest](payload=request), AgentContext()))
    second = run(agent.execute(AgentInput[EmailDraftRequest](payload=request), AgentContext()))
    assert first.result.draft.draft_id == second.result.draft.draft_id
    assert first.result.created is True
    assert second.result.created is False


def test_draft_and_human_workflow_emit_audit_events_without_body_or_address():
    store = InMemoryEmailDraftStore()
    recorder = InMemoryEventRecorder()
    _, result = make_draft(store=store, recorder=recorder)
    service = EmailApprovalService(store, event_recorder=recorder)
    run(service.apply(EmailApprovalRequest(
        draft_id=result.result.draft.draft_id,
        action=ApprovalAction(action=ApprovalActionType.EDIT, actor_id="reviewer",
                              subject="Edited", body="Changed"))))
    run(service.apply(EmailApprovalRequest(
        draft_id=result.result.draft.draft_id,
        action=ApprovalAction(action=ApprovalActionType.REJECT, actor_id="reviewer", reason="No"))))
    event_types = [event.event_type for event in recorder.events]
    assert AgentEventType.EMAIL_DRAFT_REQUESTED in event_types
    assert AgentEventType.EMAIL_DRAFT_GENERATED in event_types
    assert AgentEventType.EMAIL_SUBMITTED_FOR_REVIEW in event_types
    assert AgentEventType.EMAIL_HUMAN_EDITED in event_types
    assert AgentEventType.EMAIL_HUMAN_REJECTED in event_types
    for event in recorder.events:
        assert "body" not in event.details and "email" not in event.details


def test_email_agent_has_no_send_email_tool_permission():
    agent = EmailAgent()
    assert agent.agent_name == "email" and agent.agent_version
    context = AgentContext(permitted_tools=frozenset({"send_email", "email_provider"}))
    with pytest.raises(ToolPermissionDenied):
        agent.require_tool("send_email", context)
    assert agent.allowed_tools == frozenset()


def test_recipient_must_match_candidate_public_contact_evidence():
    request = make_request()
    request.recipient = EmailRecipient(email="other@example.org", evidence_id="ev-5")
    result = run(EmailAgent().execute(AgentInput[EmailDraftRequest](payload=request), AgentContext()))
    assert not result.success
    assert result.result.draft.status == EmailDraftStatus.BLOCKED
    assert "not verified" in result.result.draft.blocked_reason


def test_restricted_contact_source_blocks_draft():
    request = make_request()
    request.candidate.profile.source_references[0].access = "restricted"
    result = run(EmailAgent().execute(AgentInput[EmailDraftRequest](payload=request), AgentContext()))
    draft = result.result.draft
    assert not result.success
    assert draft.status == EmailDraftStatus.BLOCKED
    assert "not marked public or authorized" in draft.blocked_reason
    assert draft.personalization_points == []


def test_approved_or_rejected_draft_cannot_be_edited_again():
    store = InMemoryEmailDraftStore()
    _, result = make_draft(store=store)
    service = EmailApprovalService(store)
    approved = run(service.apply(EmailApprovalRequest(
        draft_id=result.result.draft.draft_id,
        action=ApprovalAction(action=ApprovalActionType.APPROVE, actor_id="reviewer"))))
    with pytest.raises(ValueError, match="cannot accept human action"):
        run(service.apply(EmailApprovalRequest(
            draft_id=approved.draft_id,
            action=ApprovalAction(action=ApprovalActionType.EDIT, actor_id="reviewer",
                                  subject="new", body="new"))))
