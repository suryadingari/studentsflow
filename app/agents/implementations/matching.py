"""Deterministic evidence-based matching; no crawling or inferred attributes."""

import re
from typing import Iterable

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.schemas.canonical import CanonicalStudent
from app.schemas.matching import (
    CandidateMatch, CriterionAssessment, CriterionStatus, CriterionType,
    MatchStatus, MatchingRequest, MatchingResult, UserCriterion,
)
from app.schemas.student import (
    AIInterestStatus, AIEvidenceCategory, ClaimType, Evidence, FinalYearStatus,
    SourcedFact,
)


_AI_AREA_ALIASES = {
    "machine learning": {"machine_learning"}, "ml": {"machine_learning"},
    "deep learning": {"deep_learning"}, "computer vision": {"computer_vision"}, "cv": {"computer_vision"},
    "nlp": {"nlp"}, "natural language processing": {"nlp"},
    "generative ai": {"generative_ai"}, "genai": {"generative_ai"},
    "reinforcement learning": {"reinforcement_learning"},
    "ai research": {"ai_research"}, "artificial intelligence": {"other_ai_activity"},
}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _urls(evidence: Iterable[Evidence]) -> list[str]:
    return list(dict.fromkeys(str(item.source_url) for item in evidence))


def _facts_for(profile, criterion: UserCriterion) -> list[SourcedFact]:
    fields = {
        CriterionType.LOCATION: ("location",), CriterionType.UNIVERSITY: ("university",),
        CriterionType.DEGREE: ("degree",), CriterionType.BRANCH: ("branch",),
        CriterionType.SKILL: ("skills",), CriterionType.PROJECT: ("projects",),
        CriterionType.RESEARCH: ("research_interests",),
        CriterionType.EXPERIENCE: ("projects", "research_interests", "skills"),
    }
    facts: list[SourcedFact] = []
    for field in fields.get(criterion.criterion_type, ()):
        value = getattr(profile, field)
        if isinstance(value, SourcedFact):
            facts.append(value)
        elif value:
            facts.extend(value)
    return facts


def _matches_text(value: object, target: str, *, exact: bool) -> bool:
    value_norm, target_norm = _norm(value), _norm(target)
    return value_norm == target_norm if exact else bool(target_norm and target_norm in value_norm)


class MatchingAgent(BaseAgent):
    agent_name = "matching"
    agent_version = "2.0.0"
    description = "Matches structured user criteria to validated canonical profiles with evidence and explanations."
    allowed_tools = frozenset()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> MatchingResult:
        request = agent_input.payload
        if not isinstance(request, MatchingRequest):
            request = MatchingRequest.model_validate(request)
        return MatchingResult(
            requirement=request.requirement,
            candidate_matches=[self._match_candidate(request.requirement.criteria, candidate)
                               for candidate in request.candidates],
        )

    def _match_candidate(self, criteria: list[UserCriterion], candidate: CanonicalStudent) -> CandidateMatch:
        profile = candidate.profile
        evidence_map = {item.evidence_id: item for item in profile.evidence}
        assessments: list[CriterionAssessment] = []
        for criterion in criteria:
            status, evidence, explanation = self._assess(criterion, candidate, evidence_map)
            assessments.append(CriterionAssessment(
                criterion_type=criterion.criterion_type, value=criterion.value,
                required=criterion.required, status=status, explanation=explanation,
                evidence=evidence, source_urls=_urls(evidence),
            ))

        required = [item for item in assessments if item.required]
        matched_required = [item for item in required if item.status == CriterionStatus.MATCHED]
        missing_required = [item for item in required if item.status != CriterionStatus.MATCHED]
        missing_preferred = [item for item in assessments if not item.required and item.status != CriterionStatus.MATCHED]
        uncertainties = [item.explanation for item in assessments if item.status == CriterionStatus.INSUFFICIENT_EVIDENCE]
        for conflict in candidate.conflicts:
            if any(_criterion_field(item.criterion_type) == conflict.field for item in criteria):
                uncertainties.append(f"Conflicting {conflict.field} evidence is preserved and unresolved.")

        if any(item.status == CriterionStatus.CONTRADICTED for item in required):
            status = MatchStatus.NO_MATCH
        elif not missing_required:
            status = MatchStatus.MATCH
        elif any(item.status == CriterionStatus.INSUFFICIENT_EVIDENCE for item in missing_required):
            status = MatchStatus.INSUFFICIENT_EVIDENCE
        elif matched_required:
            status = MatchStatus.PARTIAL_MATCH
        else:
            status = MatchStatus.NO_MATCH

        name = str(profile.name.value) if profile.name else None
        matched_labels = [f"{item.criterion_type.value}={item.value or 'required'}" for item in matched_required]
        missing_labels = [f"{item.criterion_type.value}={item.value or 'required'}" for item in missing_required]
        if status == MatchStatus.MATCH:
            explanation = "All required criteria are supported by the cited profile evidence."
        elif status == MatchStatus.NO_MATCH:
            explanation = "At least one required criterion is contradicted or definitively absent according to validated status."
        elif status == MatchStatus.PARTIAL_MATCH:
            explanation = "Some required criteria match, but other required profile attributes were not found in the available record."
        else:
            explanation = "The available evidence cannot verify one or more required criteria."
        if matched_labels:
            explanation += " Matched: " + ", ".join(matched_labels) + "."
        if missing_labels:
            explanation += " Missing or uncertain: " + ", ".join(missing_labels) + "."
        if missing_preferred:
            explanation += " Preferred criteria not met: " + ", ".join(
                f"{item.criterion_type.value}={item.value or 'preferred'}" for item in missing_preferred) + "."

        # Required criteria carry twice the weight of preferences. Only directly
        # evidence-matched criteria earn points; unknown or missing evidence earns 0.
        weights = [2 if item.required else 1 for item in assessments]
        possible = sum(weights)
        earned = sum(weight for item, weight in zip(assessments, weights)
                     if item.status == CriterionStatus.MATCHED)
        match_score = round(100 * earned / possible) if possible else 0

        return CandidateMatch(
            student_reference=profile.student_reference or candidate.canonical_id,
            canonical_student_id=candidate.canonical_id, candidate_name=name,
            status=status, assessments=assessments,
            match_score=match_score,
            score_explanation="Weighted evidence coverage (required criteria count double); not a calibrated probability.",
            matched_required_criteria=matched_required,
            missing_required_criteria=missing_required,
            missing_preferred_criteria=missing_preferred,
            uncertainties=list(dict.fromkeys(uncertainties)),
            conflicts=candidate.conflicts, explanation=explanation,
        )

    def _assess(self, criterion: UserCriterion, candidate: CanonicalStudent,
                evidence_map: dict[str, Evidence]) -> tuple[CriterionStatus, list[Evidence], str]:
        profile = candidate.profile
        kind = criterion.criterion_type
        target_values = [value for value in [criterion.value, *criterion.accepted_alternatives] if value]
        relevant_conflicts = [c for c in candidate.conflicts if _criterion_field(kind) == c.field]
        if relevant_conflicts:
            return (CriterionStatus.INSUFFICIENT_EVIDENCE, [],
                    f"{kind.value} cannot be verified because canonical source claims conflict.")

        if kind == CriterionType.FINAL_YEAR:
            supporting = [evidence_map[eid] for eid in _profile_claim_ids(profile, {ClaimType.ACADEMIC_YEAR, ClaimType.EXPECTED_GRADUATION_YEAR, ClaimType.GRADUATION_YEAR}) if eid in evidence_map]
            if profile.final_year_status == FinalYearStatus.FINAL_YEAR_VERIFIED and supporting:
                return CriterionStatus.MATCHED, supporting, "Final-year status is verified and linked evidence is attached."
            if profile.final_year_status == FinalYearStatus.NON_FINAL_YEAR:
                return CriterionStatus.CONTRADICTED, supporting, "Validated academic evidence classifies this candidate as non-final-year."
            if profile.final_year_status == FinalYearStatus.CONFLICTING_EVIDENCE:
                return CriterionStatus.INSUFFICIENT_EVIDENCE, supporting, "Final-year evidence conflicts and has not been resolved."
            return CriterionStatus.INSUFFICIENT_EVIDENCE, supporting, "Final-year status is unverified or lacks linked supporting evidence."

        if kind == CriterionType.AI_INTEREST:
            supporting = [e for e in profile.evidence
                          if e.claim_type == ClaimType.AI_ACTIVITY
                          or (e.claim_type == ClaimType.AI_INTEREST and e.ai_category == AIEvidenceCategory.EXPLICIT_AI_INTEREST)]
            if profile.ai_interest_status == AIInterestStatus.AI_INTEREST_SUPPORTED and supporting:
                return CriterionStatus.MATCHED, supporting, "AI interest/experience is supported by the cited AI evidence."
            if profile.ai_interest_status == AIInterestStatus.AI_INTEREST_NOT_FOUND:
                return CriterionStatus.CONTRADICTED, supporting, "Completed validation found no AI-interest or experience evidence."
            return CriterionStatus.INSUFFICIENT_EVIDENCE, supporting, "AI interest is unverified; it is not treated as a match."

        if kind == CriterionType.AI_AREA:
            matched_categories: set[str] = set()
            for value in target_values:
                key = _norm(value)
                matched_categories.update(_AI_AREA_ALIASES.get(key, {key.replace(" ", "_")}))
            found = [e for e in profile.evidence if e.ai_category is not None
                     and e.ai_category.value in matched_categories
                     and e.claim_type in {ClaimType.AI_ACTIVITY, ClaimType.AI_INTEREST}]
            if found:
                return CriterionStatus.MATCHED, found, f"Evidence specifically supports the requested AI area: {criterion.value}."
            return CriterionStatus.INSUFFICIENT_EVIDENCE, [], f"No evidence specifically supports the requested AI area: {criterion.value}."

        facts = _facts_for(profile, criterion)
        matched_ids: set[str] = set()
        if kind in {CriterionType.SKILL, CriterionType.LOCATION, CriterionType.UNIVERSITY,
                    CriterionType.DEGREE, CriterionType.BRANCH}:
            for fact in facts:
                if any(_matches_text(fact.value, value, exact=True) for value in target_values):
                    matched_ids.update(fact.evidence_ids)
        elif kind in {CriterionType.PROJECT, CriterionType.RESEARCH, CriterionType.EXPERIENCE}:
            for fact in facts:
                if any(_matches_text(fact.value, value, exact=False) for value in target_values):
                    matched_ids.update(fact.evidence_ids)
        elif kind == CriterionType.OTHER:
            for item in profile.evidence:
                if any(_matches_text(item.claim_value, value, exact=False)
                       or _matches_text(item.supporting_text, value, exact=False) for value in target_values):
                    matched_ids.add(item.evidence_id)
        supporting = [evidence_map[eid] for eid in matched_ids if eid in evidence_map]
        if supporting:
            return CriterionStatus.MATCHED, supporting, f"Source evidence supports {kind.value}: {criterion.value}."
        if facts and kind in {CriterionType.LOCATION, CriterionType.UNIVERSITY, CriterionType.DEGREE, CriterionType.BRANCH}:
            return CriterionStatus.CONTRADICTED, [], f"Explicit sourced {kind.value} information does not satisfy {criterion.value}."
        if facts:
            return CriterionStatus.MISSING, [], f"The available profile does not contain evidence for required {kind.value}: {criterion.value}."
        return CriterionStatus.INSUFFICIENT_EVIDENCE, [], f"No sourced information is available to verify {kind.value}: {criterion.value}."


def _profile_claim_ids(profile, claim_types: set[ClaimType]) -> set[str]:
    return {item.evidence_id for item in profile.evidence if item.claim_type in claim_types}


def _criterion_field(kind: CriterionType) -> str:
    return {
        CriterionType.FINAL_YEAR: "graduation_year",
        CriterionType.LOCATION: "location", CriterionType.UNIVERSITY: "university",
        CriterionType.DEGREE: "degree", CriterionType.BRANCH: "branch",
        CriterionType.SKILL: "skills", CriterionType.PROJECT: "projects",
        CriterionType.RESEARCH: "research_interests",
    }.get(kind, kind.value)
