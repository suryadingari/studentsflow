import asyncio

from app.agents.context import AgentContext
from app.agents.implementations.deduplication import DeduplicationAgent
from app.agents.implementations.enrichment import EnrichmentAgent
from app.agents.models import AgentInput
from app.schemas.canonical import (
    DeduplicationDecisionType, DeduplicationRequest, EnrichmentRequest, EnrichmentSource,
)
from app.schemas.student import (
    AIInterestStatus, ClaimType, Evidence, EvidenceType, FinalYearStatus,
    SourceReference, SourcedFact, StudentProfile,
)


def run(coro):
    return asyncio.run(coro)


def profile(url: str, *, name="John Doe", university="University X", year=2027, github=None,
            portfolio=None, projects=(), final=FinalYearStatus.FINAL_YEAR_VERIFIED,
            ai=AIInterestStatus.AI_INTEREST_UNVERIFIED):
    evidence = []

    def fact(claim, value, kind=EvidenceType.PUBLIC_PROFILE):
        eid = f"{url}:{claim.value}:{value}"
        evidence.append(Evidence(
            evidence_id=eid, claim_type=claim, claim_value=value, evidence_type=kind,
            source_url=url, source_title="profile", supporting_text=str(value),
        ))
        return SourcedFact(value=value, evidence_ids=[eid])

    facts = {
        "name": fact(ClaimType.NAME, name) if name else None,
        "university": fact(ClaimType.UNIVERSITY, university) if university else None,
        "expected_graduation_year": fact(ClaimType.EXPECTED_GRADUATION_YEAR, year) if year else None,
        "github": fact(ClaimType.PROFILE_URL, github, EvidenceType.GITHUB_REPOSITORY) if github else None,
        "portfolio": fact(ClaimType.PROFILE_URL, portfolio, EvidenceType.PORTFOLIO) if portfolio else None,
    }
    project_facts = [fact(ClaimType.PROJECT, value, EvidenceType.PROJECT_PAGE) for value in projects]
    return StudentProfile(
        **facts, projects=project_facts, final_year_status=final, ai_interest_status=ai,
        evidence=evidence, source_references=[SourceReference(source_url=url, title="profile")],
    )


def dedupe(*profiles):
    return run(DeduplicationAgent().execute(
        AgentInput[DeduplicationRequest](payload=DeduplicationRequest(profiles=list(profiles))), AgentContext())).result


def enrich(canonical, addition):
    return run(EnrichmentAgent().execute(
        AgentInput[EnrichmentRequest](payload=EnrichmentRequest(
            canonical_student=canonical,
            additions=[EnrichmentSource(extracted_profile=addition, source_url=str(addition.source_references[0].source_url))],
        )), AgentContext())).result


def test_same_person_requires_multiple_correlated_signals_and_explains_decision():
    a = profile("https://uni.example/john", github="https://github.com/johndoe-ai")
    b = profile("https://github.com/johndoe-ai", github="https://github.com/johndoe-ai")
    result = dedupe(a, b)
    decision = result.decisions[0]
    assert decision.decision == DeduplicationDecisionType.SAME_PERSON
    assert decision.confidence.value == "high"
    assert {signal.field for signal in decision.signals if signal.match} >= {"name", "github"}
    assert decision.evidence_references
    assert len(result.canonical_students) == 1


def test_same_name_different_university_stays_separate():
    result = dedupe(profile("https://a.example", university="University X"),
                    profile("https://b.example", university="University Y"))
    assert result.decisions[0].decision == DeduplicationDecisionType.DIFFERENT_PERSON
    assert len(result.canonical_students) == 2


def test_different_github_accounts_prevent_automatic_merge():
    result = dedupe(profile("https://a.example", github="https://github.com/john-one"),
                    profile("https://b.example", github="https://github.com/john-two"))
    assert result.decisions[0].decision == DeduplicationDecisionType.POSSIBLE_DUPLICATE
    assert len(result.canonical_students) == 2


def test_three_sources_form_one_canonical_profile_and_preserve_all_sources():
    a = profile("https://uni.example/john")
    b = profile("https://github.com/johndoe", name="John Doe", university="University X",
                github="https://github.com/johndoe")
    c = profile("https://john.example", name="John Doe", university="University X",
                portfolio="https://john.example")
    result = dedupe(a, b, c)
    assert all(item.decision == DeduplicationDecisionType.SAME_PERSON for item in result.decisions)
    assert len(result.canonical_students) == 1
    canonical = result.canonical_students[0]
    assert len(canonical.source_profiles) == 3
    assert set(canonical.source_profile_references) == {
        "https://uni.example/john", "https://github.com/johndoe", "https://john.example/"
    }
    assert len(canonical.profile.evidence) == sum(len(p.evidence) for p in (a, b, c))


def test_conflicting_source_facts_are_retained_as_canonical_conflicts():
    a = profile("https://uni.example/john", year=2027)
    b = profile("https://resume.example/john", year=2026)
    canonical = dedupe(a, b).canonical_students
    # The disagreement remains visible even when these records were not merged.
    assert len(canonical) == 2
    merged = dedupe(profile("https://a.example", year=2027, github="https://github.com/john"),
                    profile("https://b.example", year=2026, github="https://github.com/john")).canonical_students[0]
    conflict = next(item for item in merged.conflicts if item.field == "expected_graduation_year")
    assert set(conflict.values) == {2027, 2026}
    assert len(conflict.evidence_ids) == 2


def test_enrichment_adds_ai_project_with_source_provenance_without_changing_status():
    canonical = dedupe(profile("https://uni.example/john", github="https://github.com/john")).canonical_students[0]
    addition = profile("https://portfolio.example/projects", name="John Doe", university=None,
                       year=None, projects=["Computer Vision project using YOLO"],
                       ai=AIInterestStatus.AI_INTEREST_SUPPORTED)
    result = enrich(canonical, addition)
    assert "Computer Vision project using YOLO" in [fact.value for fact in result.canonical_student.profile.projects]
    project = next(e for e in result.canonical_student.profile.evidence if e.claim_type == ClaimType.PROJECT)
    assert str(project.source_url) == "https://portfolio.example/projects"
    assert "https://portfolio.example/projects" in result.canonical_student.source_profile_references
    assert result.requires_validation
    assert result.canonical_student.profile.ai_interest_status == AIInterestStatus.AI_INTEREST_UNVERIFIED


def test_enrichment_preserves_graduation_conflict_without_overwriting():
    canonical = dedupe(profile("https://uni.example/john", year=2027, github="https://github.com/john")).canonical_students[0]
    addition = profile("https://portfolio.example", name="John Doe", university=None, year=2026)
    result = enrich(canonical, addition)
    conflict = next(item for item in result.conflicts if item.field == "expected_graduation_year")
    assert set(conflict.values) == {2027, 2026}
    assert result.canonical_student.profile.expected_graduation_year.value == 2027
    assert len(result.canonical_student.profile.evidence) > len(canonical.profile.evidence)
    assert result.requires_validation


def test_empty_enrichment_does_not_fabricate_or_change_profile():
    canonical = dedupe(profile("https://uni.example/john")).canonical_students[0]
    empty = StudentProfile(source_references=[SourceReference(source_url="https://empty.example")])
    result = enrich(canonical, empty)
    assert result.added_evidence_ids == []
    assert result.added_facts == []
    assert result.canonical_student.profile.name.value == "John Doe"
    assert result.notes
    assert not result.requires_validation


def test_enrichment_skips_restricted_source():
    canonical = dedupe(profile("https://uni.example/john")).canonical_students[0]
    restricted = profile("https://restricted.example", name="John Doe", university=None, year=None,
                         projects=["AI project"])
    result = run(EnrichmentAgent().execute(
        AgentInput[EnrichmentRequest](payload=EnrichmentRequest(
            canonical_student=canonical,
            additions=[EnrichmentSource(extracted_profile=restricted,
                                        source_url="https://restricted.example", access="restricted")],
        )), AgentContext())).result
    assert not result.added_evidence_ids
    assert "not marked public or authorized" in result.notes[0]
