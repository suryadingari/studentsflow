"""Evidence-grounded draft creation and human review state; no sending capability."""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.events import AgentEvent, AgentEventType, EventRecorder
from app.agents.models import AgentInput
from app.schemas.email import (
    ApprovalAction, ApprovalActionType, ApprovalRecord, EmailApprovalRequest,
    EmailDraft, EmailDraftRequest, EmailDraftResult, EmailDraftStatus,
    EmailDraftVersion, EmailEvidenceReference, EmailPersonalizationPoint,
)
from app.schemas.matching import CandidateMatch, CriterionStatus, CriterionType, MatchStatus
from app.schemas.student import ClaimType, Evidence, EvidenceType
from app.schemas.student import AIEvidenceCategory, EvidenceStrength


class InMemoryEmailDraftStore:
    """Process-local workflow state for development/tests; not durable storage."""

    def __init__(self) -> None:
        self._drafts: dict[str, EmailDraft] = {}
        self._fingerprints: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_or_get(self, draft: EmailDraft) -> tuple[EmailDraft, bool]:
        async with self._lock:
            prior_id = self._fingerprints.get(draft.request_fingerprint)
            if prior_id is not None:
                return self._drafts[prior_id].model_copy(deep=True), False
            self._drafts[draft.draft_id] = draft.model_copy(deep=True)
            self._fingerprints[draft.request_fingerprint] = draft.draft_id
            return draft.model_copy(deep=True), True

    async def get(self, draft_id: str) -> EmailDraft:
        async with self._lock:
            if draft_id not in self._drafts:
                raise KeyError(f"Unknown email draft: {draft_id}")
            return self._drafts[draft_id].model_copy(deep=True)

    async def update(
        self, draft_id: str, transition: Callable[[EmailDraft], tuple[EmailDraft, bool]]
    ) -> tuple[EmailDraft, bool]:
        async with self._lock:
            if draft_id not in self._drafts:
                raise KeyError(f"Unknown email draft: {draft_id}")
            updated, changed = transition(self._drafts[draft_id].model_copy(deep=True))
            if changed:
                self._drafts[draft_id] = updated.model_copy(deep=True)
            return self._drafts[draft_id].model_copy(deep=True), changed


class EmailAgent(BaseAgent):
    agent_name = "email"
    agent_version = "2.0.0"
    description = "Creates evidence-grounded email drafts and submits them for human review; never sends email."
    allowed_tools = frozenset()

    def __init__(self, *, draft_store: InMemoryEmailDraftStore | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.draft_store = draft_store or InMemoryEmailDraftStore()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> EmailDraftResult:
        request = agent_input.payload
        if not isinstance(request, EmailDraftRequest):
            request = EmailDraftRequest.model_validate(request)
        fingerprint_material = request.request_id or json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        fingerprint = hashlib.sha256(fingerprint_material.encode()).hexdigest()
        await self._record(AgentEventType.EMAIL_DRAFT_REQUESTED, context,
                           {"request_fingerprint": fingerprint})

        blocked_reason = _blocked_reason(request)
        if blocked_reason:
            draft = EmailDraft(
                draft_id=f"draft-{fingerprint[:20]}", request_fingerprint=fingerprint,
                candidate_id=request.candidate.canonical_id, recipient=request.recipient,
                status=EmailDraftStatus.BLOCKED, blocked_reason=blocked_reason,
                signature=request.signature,
            )
            stored, created = await self.draft_store.create_or_get(draft)
            await self._record(AgentEventType.EMAIL_DRAFT_BLOCKED, context,
                               {"draft_id": stored.draft_id, "reason_code": _reason_code(blocked_reason)})
            return EmailDraftResult(draft=stored, created=created)

        contact_evidence = next(item for item in request.candidate.profile.evidence
                                if item.evidence_id == request.recipient.evidence_id)
        contact_source = next(item for item in request.candidate.profile.source_references
                              if str(item.source_url) == str(contact_evidence.source_url))
        authorized_recipient = request.recipient.model_copy(update={
            "source_url": str(contact_evidence.source_url),
            "source_access": contact_source.access,
        })

        points = _personalization_points(
            request.requirement.criteria, request.match, request.candidate.profile.evidence,
            request.candidate.profile.source_references,
        )
        # Names are not used unless a future validation contract explicitly
        # certifies them; the generic greeting avoids an unverified identity claim.
        greeting = "Hello,"
        subject = "A question about your technical work"
        paragraphs = [point.statement for point in points]
        paragraphs.append(
            "I’m reaching out to ask whether you might be open to a brief conversation about your work and interests."
        )
        call_to_action = "Would you be available for a brief conversation?"
        paragraphs.append(call_to_action)
        paragraphs.append(f"Best,\n{request.signature.strip() or 'Research Team'}")
        body = greeting + "\n\n" + "\n\n".join(paragraphs)
        all_references = [ref for point in points for ref in point.evidence_references]
        draft = EmailDraft(
            draft_id=f"draft-{fingerprint[:20]}", request_fingerprint=fingerprint,
            candidate_id=request.candidate.canonical_id, recipient=authorized_recipient,
            generated_subject=subject, generated_body=body, subject=subject, body=body,
            greeting=greeting, call_to_action=call_to_action,
            signature=request.signature.strip() or "Research Team",
            personalization_summary=[point.statement for point in points],
            personalization_points=points, evidence_references=all_references,
            status=EmailDraftStatus.PENDING_REVIEW,
            versions=[EmailDraftVersion(version_number=1, subject=subject, body=body,
                                        authored_by="email-agent", evidence_references=all_references)],
        )
        stored, created = await self.draft_store.create_or_get(draft)
        if created:
            await self._record(AgentEventType.EMAIL_DRAFT_GENERATED, context,
                               {"draft_id": stored.draft_id, "evidence_reference_count": len(all_references)})
            await self._record(AgentEventType.EMAIL_SUBMITTED_FOR_REVIEW, context,
                               {"draft_id": stored.draft_id, "version": 1})
        return EmailDraftResult(draft=stored, created=created)


class EmailApprovalService:
    """Human-only state transitions. This service has no transport/sending method."""

    def __init__(self, draft_store: InMemoryEmailDraftStore, *, event_recorder: EventRecorder | None = None) -> None:
        self.draft_store = draft_store
        self.event_recorder = event_recorder

    async def apply(self, request: EmailApprovalRequest, context: AgentContext | None = None) -> EmailDraft:
        context = context or AgentContext()
        action = request.action
        event_type = {
            ApprovalActionType.APPROVE: AgentEventType.EMAIL_HUMAN_APPROVED,
            ApprovalActionType.EDIT: AgentEventType.EMAIL_HUMAN_EDITED,
            ApprovalActionType.REJECT: AgentEventType.EMAIL_HUMAN_REJECTED,
        }[action.action]

        def transition(draft: EmailDraft) -> tuple[EmailDraft, bool]:
            if any(record.action_id == action.action_id for record in draft.approvals):
                return draft, False
            if draft.status not in {EmailDraftStatus.PENDING_REVIEW, EmailDraftStatus.EDITED}:
                raise ValueError(f"Draft in {draft.status.value} state cannot accept human action")
            now = datetime.now(timezone.utc)
            if action.action == ApprovalActionType.EDIT:
                if not action.subject or not action.subject.strip() or not action.body or not action.body.strip():
                    raise ValueError("human edit requires non-empty subject and body")
                version = EmailDraftVersion(
                    version_number=len(draft.versions) + 1, subject=action.subject.strip(),
                    body=action.body.strip(), authored_by=f"human:{action.actor_id}",
                    created_at=now, evidence_references=draft.evidence_references,
                )
                draft.versions.append(version)
                draft.subject, draft.body = version.subject, version.body
                draft.status = EmailDraftStatus.EDITED
            elif action.action == ApprovalActionType.APPROVE:
                draft.status = EmailDraftStatus.APPROVED
                draft.approved_subject, draft.approved_body = draft.subject, draft.body
            else:
                draft.status = EmailDraftStatus.REJECTED
            draft.approvals.append(ApprovalRecord(
                action_id=action.action_id, action=action.action, actor_id=action.actor_id,
                occurred_at=now, version_number=len(draft.versions), reason=action.reason,
            ))
            draft.updated_at = now
            return draft, True

        draft, changed = await self.draft_store.update(request.draft_id, transition)
        if changed and self.event_recorder is not None:
            await self.event_recorder.record(AgentEvent(
                event_type=event_type, execution_id=context.execution_id,
                workflow_id=context.workflow_id, workflow_step_id=context.workflow_step_id,
                agent_name="email", agent_version=EmailAgent.agent_version,
                details={"draft_id": draft.draft_id, "version": len(draft.versions),
                         "actor_id": action.actor_id},
            ))
        return draft


def _blocked_reason(request: EmailDraftRequest) -> str | None:
    if request.match.canonical_student_id != request.candidate.canonical_id:
        return "Matching result does not refer to the supplied canonical candidate."
    criteria_keys = {(criterion.criterion_type, _normalize(criterion.value))
                     for criterion in request.requirement.criteria}
    assessment_keys = {(assessment.criterion_type, _normalize(assessment.value))
                       for assessment in request.match.assessments}
    if not criteria_keys.issubset(assessment_keys):
        return "Matching result does not assess every criterion in the supplied requirement."
    if request.match.status == MatchStatus.MATCH and any(
        assessment.status != CriterionStatus.MATCHED
        for assessment in request.match.assessments if assessment.required
    ):
        return "Matching result marks a candidate as a full match while a required criterion is not matched."
    if request.match.status == MatchStatus.NO_MATCH:
        return "Candidate did not satisfy the required matching criteria."
    recipient = request.recipient
    contact = request.candidate.profile.public_contact
    if recipient is None or contact is None:
        return "No authorized recipient email address is available in the supplied candidate data."
    if recipient.email.lower() != str(contact.value).strip().lower() or recipient.evidence_id not in contact.evidence_ids:
        return "Recipient email is not verified by the supplied candidate contact evidence."
    evidence = next((item for item in request.candidate.profile.evidence
                     if item.evidence_id == recipient.evidence_id), None)
    if evidence is None or evidence.claim_type != ClaimType.PUBLIC_CONTACT or str(evidence.claim_value).lower() != recipient.email.lower():
        return "Recipient email has no matching public-contact evidence record."
    source = next((item for item in request.candidate.profile.source_references
                   if str(item.source_url) == str(evidence.source_url)), None)
    if source is None or source.access not in {"public", "authorized", "public_or_authorized"}:
        return "Recipient contact source is not marked public or authorized."
    return None


def _personalization_points(requirement_criteria, match: CandidateMatch,
                            candidate_evidence: list[Evidence], source_references) -> list[EmailPersonalizationPoint]:
    evidence_map = {item.evidence_id: item for item in candidate_evidence}
    source_access = {str(item.source_url): item.access for item in source_references}
    requested = {(criterion.criterion_type, _normalize(criterion.value)) for criterion in requirement_criteria}
    points: list[EmailPersonalizationPoint] = []
    used: set[tuple[str, str]] = set()
    for assessment in match.assessments:
        if (assessment.status != CriterionStatus.MATCHED
                or (assessment.criterion_type, _normalize(assessment.value)) not in requested):
            continue
        verified = [evidence_map[item.evidence_id] for item in assessment.evidence
                    if item.evidence_id in evidence_map
                    and evidence_map[item.evidence_id].source_url == item.source_url
                    and source_access.get(str(evidence_map[item.evidence_id].source_url))
                    in {"public", "authorized", "public_or_authorized"}]
        if assessment.criterion_type == CriterionType.FINAL_YEAR:
            verified = [item for item in verified if item.claim_type in {
                ClaimType.ACADEMIC_YEAR, ClaimType.EXPECTED_GRADUATION_YEAR, ClaimType.GRADUATION_YEAR
            }]
        elif assessment.criterion_type == CriterionType.AI_INTEREST:
            verified = [item for item in verified
                        if (item.claim_type == ClaimType.AI_ACTIVITY
                            and item.strength in {EvidenceStrength.HIGH, EvidenceStrength.MEDIUM})
                        or (item.claim_type == ClaimType.AI_INTEREST
                            and item.ai_category == AIEvidenceCategory.EXPLICIT_AI_INTEREST
                            and item.strength in {EvidenceStrength.HIGH, EvidenceStrength.MEDIUM})]
        elif assessment.criterion_type == CriterionType.SKILL:
            verified = [item for item in verified if item.claim_type == ClaimType.SKILL]
        elif assessment.criterion_type in {CriterionType.PROJECT, CriterionType.AI_AREA}:
            verified = [item for item in verified if item.claim_type in {ClaimType.PROJECT, ClaimType.AI_ACTIVITY}]
        elif assessment.criterion_type == CriterionType.RESEARCH:
            verified = [item for item in verified if item.claim_type == ClaimType.RESEARCH_INTEREST]
        else:
            verified = []
        if not verified:
            continue
        refs = [_email_reference(assessment.criterion_type.value, item) for item in verified]
        ref_key = (assessment.criterion_type.value, ",".join(sorted(item.evidence_id for item in verified)))
        if ref_key in used:
            continue
        used.add(ref_key)
        claim = str(verified[0].claim_value).strip().replace("\n", " ")[:240]
        criterion = assessment.criterion_type
        if criterion == CriterionType.SKILL:
            statement = f"Your profile lists {claim} as a skill."
        elif criterion == CriterionType.FINAL_YEAR:
            item = verified[0]
            if item.claim_type in {ClaimType.EXPECTED_GRADUATION_YEAR, ClaimType.GRADUATION_YEAR}:
                year_label = ("expected graduation year" if item.claim_type == ClaimType.EXPECTED_GRADUATION_YEAR
                              else "graduation year")
                statement = f"Your profile lists {item.claim_value} as your {year_label}."
            else:
                statement = "Your profile indicates that you are in your final year."
        elif criterion == CriterionType.AI_INTEREST:
            statement = f"Your profile includes the following AI-related work: “{claim}”."
        elif criterion == CriterionType.RESEARCH:
            statement = f"Your research profile mentions “{claim}”."
        else:
            statement = f"I noticed this project or technical work in your profile: “{claim}”."
        points.append(EmailPersonalizationPoint(
            criterion_type=criterion.value, statement=statement, evidence_references=refs,
        ))
    return points


def _email_reference(criterion: str, evidence: Evidence) -> EmailEvidenceReference:
    return EmailEvidenceReference(
        criterion_type=criterion, evidence_id=evidence.evidence_id,
        source_url=str(evidence.source_url), supporting_text=evidence.supporting_text,
        claim_value=evidence.claim_value,
    )


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _reason_code(reason: str) -> str:
    if "recipient" in reason.lower() or "contact" in reason.lower():
        return "recipient_unavailable_or_unauthorized"
    if "matching" in reason.lower():
        return "candidate_not_eligible"
    return "request_invalid"
