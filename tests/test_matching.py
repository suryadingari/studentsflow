import asyncio

import pytest

from app.agents.context import AgentContext
from app.agents.implementations.matching import MatchingAgent
from app.agents.models import AgentInput
from app.schemas.canonical import CanonicalConflict, CanonicalStudent
from app.schemas.matching import (
    CriterionStatus, CriterionType, MatchStatus, MatchingRequest, UserCriterion,
    UserRequirement,
)
from app.schemas.student import (
    AIInterestStatus, AIEvidenceCategory, ClaimType, Evidence, EvidenceType,
    FinalYearStatus, SourceReference, SourcedFact, StudentProfile,
)


def run(coro):
    return asyncio.run(coro)


def candidate(*, branch=None, final=FinalYearStatus.FINAL_YEAR_VERIFIED,
              ai=AIInterestStatus.AI_INTEREST_SUPPORTED, skills=("Python", "PyTorch"),
              area=AIEvidenceCategory.COMPUTER_VISION, location="India", conflicts=None):
    url = "https://public.example/student"
    evidence = []

    def add(claim, value, category=None, evidence_type=EvidenceType.PUBLIC_PROFILE):
        eid = f"e{len(evidence)}"
        evidence.append(Evidence(
            evidence_id=eid, claim_type=claim, claim_value=value,
            evidence_type=evidence_type, source_url=url,
            supporting_text=str(value), ai_category=category,
        ))
        return SourcedFact(value=value, evidence_ids=[eid])

    year_claim = add(ClaimType.EXPECTED_GRADUATION_YEAR, 2027)
    skill_facts = [add(ClaimType.SKILL, skill) for skill in skills]
    project = add(ClaimType.AI_ACTIVITY, "Computer Vision object detection project", area,
                  EvidenceType.PROJECT_PAGE)
    location_fact = add(ClaimType.LOCATION, location) if location else None
    fields = {"branch": add(ClaimType.BRANCH, branch) if branch else None}
    profile = StudentProfile(
        name=add(ClaimType.NAME, "Alex Student"),
        expected_graduation_year=year_claim,
        skills=skill_facts,
        projects=[add(ClaimType.PROJECT, "Computer Vision object detection project", area,
                      EvidenceType.PROJECT_PAGE)],
        location=location_fact,
        final_year_status=final, ai_interest_status=ai,
        evidence=evidence, source_references=[SourceReference(source_url=url)], **fields,
    )
    return CanonicalStudent(
        canonical_id="canonical-1", profile=profile, source_profiles=[profile],
        source_profile_references=[url], conflicts=conflicts or [],
    )


def criteria(*, require_area=True, preferred=False):
    return [
        UserCriterion(criterion_type=CriterionType.FINAL_YEAR, required=True),
        UserCriterion(criterion_type=CriterionType.AI_INTEREST, required=True),
        UserCriterion(criterion_type=CriterionType.SKILL, value="Python", required=True),
        UserCriterion(criterion_type=CriterionType.SKILL, value="PyTorch", required=not preferred),
        UserCriterion(criterion_type=CriterionType.AI_AREA, value="Computer Vision",
                      required=not preferred if require_area else True),
    ]


def match(student, criterion_list=None):
    requirement = UserRequirement(raw_text="test requirement", criteria=criterion_list or criteria())
    output = run(MatchingAgent().execute(
        AgentInput[MatchingRequest](payload=MatchingRequest(requirement=requirement, candidates=[student])),
        AgentContext(),
    ))
    assert output.success
    return output.result.candidate_matches[0]


def test_full_match_contains_evidence_for_each_criterion():
    result = match(candidate())
    assert result.status == MatchStatus.MATCH
    assert len(result.matched_required_criteria) == 5
    assert all(item.evidence and item.source_urls for item in result.matched_required_criteria)
    assert "All required criteria" in result.explanation
    assert result.match_score == 100
    assert "not a calibrated probability" in result.score_explanation


def test_missing_required_skill_is_reported_as_partial_match():
    result = match(candidate(skills=("Python",)))
    assert result.status == MatchStatus.PARTIAL_MATCH
    assert any(item.value == "PyTorch" for item in result.missing_required_criteria)


def test_unverified_ai_interest_is_insufficient_evidence():
    result = match(candidate(ai=AIInterestStatus.AI_INTEREST_UNVERIFIED))
    assert result.status == MatchStatus.INSUFFICIENT_EVIDENCE
    ai = next(item for item in result.assessments if item.criterion_type == CriterionType.AI_INTEREST)
    assert ai.status == CriterionStatus.INSUFFICIENT_EVIDENCE
    assert result.match_score < 100


def test_non_final_year_is_a_definitive_no_match():
    result = match(candidate(final=FinalYearStatus.NON_FINAL_YEAR))
    assert result.status == MatchStatus.NO_MATCH
    assert result.missing_required_criteria[0].status == CriterionStatus.CONTRADICTED


def test_mechanical_engineering_candidate_matches_based_on_evidence():
    result = match(candidate(branch="Mechanical Engineering"))
    assert result.status == MatchStatus.MATCH


def test_conflicting_final_year_evidence_is_insufficient():
    conflict = CanonicalConflict(field="graduation_year", values=[2026, 2027],
                                 evidence_ids=["e0"], explanation="Conflicting source years")
    result = match(candidate(final=FinalYearStatus.CONFLICTING_EVIDENCE, conflicts=[conflict]))
    assert result.status == MatchStatus.INSUFFICIENT_EVIDENCE
    assert result.conflicts == [conflict]
    assert result.uncertainties


def test_missing_preferred_criteria_do_not_prevent_match():
    result = match(candidate(skills=("Python",), area=AIEvidenceCategory.MACHINE_LEARNING), criteria(preferred=True))
    assert result.status == MatchStatus.MATCH
    assert {item.value for item in result.missing_preferred_criteria} == {"PyTorch", "Computer Vision"}


def test_location_requires_explicit_matching_evidence():
    required = [UserCriterion(criterion_type=CriterionType.LOCATION, value="India")]
    assert match(candidate(location=None), required).status == MatchStatus.INSUFFICIENT_EVIDENCE
    assert match(candidate(location="Canada"), required).status == MatchStatus.NO_MATCH


def test_ai_project_does_not_satisfy_unrelated_ai_area():
    result = match(candidate(area=AIEvidenceCategory.MACHINE_LEARNING), [
        UserCriterion(criterion_type=CriterionType.AI_AREA, value="Computer Vision")
    ])
    assert result.status == MatchStatus.INSUFFICIENT_EVIDENCE
    assert result.assessments[0].status == CriterionStatus.INSUFFICIENT_EVIDENCE


def test_skill_matching_is_exact_without_declared_alternatives():
    result = match(candidate(skills=("Python", "TensorFlow")), [
        UserCriterion(criterion_type=CriterionType.SKILL, value="PyTorch")
    ])
    assert result.status == MatchStatus.NO_MATCH


def test_requirement_can_explicitly_allow_skill_alternative():
    result = match(candidate(skills=("TensorFlow",)), [
        UserCriterion(criterion_type=CriterionType.SKILL, value="PyTorch",
                      accepted_alternatives=["TensorFlow"])
    ])
    assert result.status == MatchStatus.MATCH
