import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.agents.context import AgentContext
from app.agents.implementations.extraction import ExtractionAgent
from app.agents.implementations.validation import ValidationAgent
from app.agents.models import AgentInput
from app.crawling.models import CrawlMetadata, CrawlResult
from app.schemas.student import (
    AIInterestStatus, ClaimType, Evidence, EvidenceType, ExtractedStudentInformation,
    FinalYearStatus, StudentProfile, ValidationRequest,
)


def run(coro):
    return asyncio.run(coro)


def crawl(content: str, url: str = "https://university.example/profile", title: str = "University Student Profile") -> CrawlResult:
    return CrawlResult(
        requested_url=url, final_url=url, status_code=200, page_title=title,
        raw_content=content, success=True,
        provenance=CrawlMetadata(source_url=url, final_url=url, tool_version="test"),
    )


def extract_and_validate(content: str, *, complete: bool = True, year: int = 2026):
    extraction = run(ExtractionAgent().execute(
        AgentInput[CrawlResult](payload=crawl(content)), AgentContext()))
    assert extraction.success
    validation = run(ValidationAgent().execute(
        AgentInput[ValidationRequest](payload=ValidationRequest(
            extracted=extraction.result, research_complete=complete, current_year=year)), AgentContext()))
    assert validation.success
    return extraction.result, validation.result


@pytest.mark.parametrize("branch", ["Computer Science", "Mechanical Engineering"])
def test_final_year_and_computer_vision_supported_for_any_branch(branch):
    extracted, result = extract_and_validate(
        f"Name: Alex Student\nBranch: {branch}\nExpected graduation: 2027\n"
        "Project: Computer Vision based manufacturing defect detection")
    assert result.final_year_status == FinalYearStatus.FINAL_YEAR_VERIFIED
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_SUPPORTED
    ai = next(e for e in extracted.evidence if e.claim_type == ClaimType.AI_ACTIVITY)
    assert ai.ai_category.value == "computer_vision"
    assert str(ai.source_url) == "https://university.example/profile"
    assert ai.supporting_text in "Project: Computer Vision based manufacturing defect detection"
    assert result.profile.branch.value == branch


def test_final_year_mechanical_cad_only_is_ai_not_found_after_complete_research():
    _, result = extract_and_validate(
        "Branch: Mechanical Engineering\nCurrent academic year: final year\nProject: CAD-based mechanical design")
    assert result.final_year_status == FinalYearStatus.FINAL_YEAR_VERIFIED
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_NOT_FOUND


def test_ai_project_without_final_year_evidence_is_supported_but_final_year_unverified():
    _, result = extract_and_validate("Project: Machine Learning based crop disease classification")
    assert result.final_year_status == FinalYearStatus.FINAL_YEAR_UNVERIFIED
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_SUPPORTED


def test_explicit_nonfinal_year_with_ai_project():
    _, result = extract_and_validate(
        "Current academic year: second year\nProject: Deep Learning image classifier")
    assert result.final_year_status == FinalYearStatus.NON_FINAL_YEAR
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_SUPPORTED


def test_conflicting_graduation_sources_are_not_silently_resolved():
    base, _ = extract_and_validate("Expected graduation: 2027")
    second = run(ExtractionAgent().execute(
        AgentInput[CrawlResult](payload=crawl("Expected graduation: 2028", "https://resume.example/cv", "Resume")), AgentContext()))
    merged = ExtractedStudentInformation(
        evidence=[*base.evidence, *second.result.evidence],
        source_references=[*base.source_references, *second.result.source_references],
    )
    result = run(ValidationAgent().execute(
        AgentInput[ValidationRequest](payload=ValidationRequest(extracted=merged, research_complete=True, current_year=2026)), AgentContext())).result
    assert result.final_year_status == FinalYearStatus.CONFLICTING_EVIDENCE
    conflict = next(item for item in result.conflicting_claims if item.claim_type == ClaimType.EXPECTED_GRADUATION_YEAR)
    assert set(conflict.values) == {2027, 2028}
    assert len(conflict.evidence_ids) == 2


def test_expected_and_attained_graduation_year_disagreement_is_conflicting():
    expected = run(ExtractionAgent().execute(
        AgentInput[CrawlResult](payload=crawl("Expected graduation: 2027")), AgentContext())).result
    attained = run(ExtractionAgent().execute(
        AgentInput[CrawlResult](payload=crawl("Graduation year: 2026", "https://resume.example/cv", "Resume")), AgentContext())).result
    merged = ExtractedStudentInformation(evidence=[*expected.evidence, *attained.evidence])
    result = run(ValidationAgent().execute(
        AgentInput[ValidationRequest](payload=ValidationRequest(extracted=merged, current_year=2026)), AgentContext())).result
    assert result.final_year_status == FinalYearStatus.CONFLICTING_EVIDENCE


def test_missing_ai_is_unverified_when_research_incomplete():
    _, result = extract_and_validate("Project: CAD-based mechanical design", complete=False)
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_UNVERIFIED


def test_python_alone_does_not_support_ai_interest():
    _, result = extract_and_validate("Skills: Python, CAD\nBranch: Computer Science")
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_NOT_FOUND


def test_html_source_and_multiple_explicit_name_sections_produce_separate_evidence_profiles():
    content = ("<main><section><h2>Name: Alex Student</h2><p>Expected graduation: 2027</p>"
               "<p>Project: Computer Vision traffic sign classifier</p></section>"
               "<section><h2>Name: Sam Student</h2><p>Current academic year: third year</p>"
               "<p>Project: CAD machine design</p></section><script>Name: Fake</script></main>")
    result = run(ExtractionAgent().execute(
        AgentInput[CrawlResult](payload=crawl(content)), AgentContext())).result

    assert result.profile.name.value == "Alex Student"
    assert len(result.additional_candidates) == 1
    second = result.additional_candidates[0]
    assert second.profile.name.value == "Sam Student"
    assert second.profile.projects[0].value == "CAD machine design"
    assert all("Fake" not in evidence.supporting_text for evidence in [*result.evidence, *second.evidence])


def test_keyword_without_personal_activity_is_unverified():
    _, result = extract_and_validate("This course introduces artificial intelligence concepts.")
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_UNVERIFIED
    assert result.unsupported_claims


def test_multiple_claims_keep_evidence_and_source_provenance():
    extracted, result = extract_and_validate(
        "Name: Alex Student\nUniversity: Example University\nSkills: Python, SQL\n"
        "Project: NLP based student feedback analysis\nExpected graduation: 2027")
    assert len(extracted.evidence) >= 7
    assert all(str(item.source_url) == "https://university.example/profile" for item in extracted.evidence)
    assert all(fact.evidence_ids for fact in extracted.profile.skills + extracted.profile.projects)
    assert result.evidence_references == extracted.evidence
    assert result.profile.name.value == "Alex Student"


def test_no_student_information_does_not_fabricate_profile():
    extracted, result = extract_and_validate("Welcome to our company page.")
    assert not extracted.evidence
    assert extracted.profile.name is None
    assert extracted.profile.projects == []
    assert result.final_year_status == FinalYearStatus.FINAL_YEAR_UNVERIFIED
    assert result.ai_interest_status == AIInterestStatus.AI_INTEREST_NOT_FOUND


def test_sourced_fact_must_reference_evidence_in_profile():
    with pytest.raises(ValidationError, match="every sourced fact"):
        StudentProfile(name={"value": "Invented", "evidence_ids": ["missing"]})


def test_ai_interest_and_final_year_status_are_structured_enums():
    _, result = extract_and_validate("Current academic year: final year\nProject: Computer Vision robot navigation")
    assert isinstance(result.final_year_status, FinalYearStatus)
    assert isinstance(result.ai_interest_status, AIInterestStatus)
    assert result.validated_claims
