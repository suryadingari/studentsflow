"""Evidence-linked student facts and validation results for Step 5."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, model_validator


class ClaimType(StrEnum):
    NAME = "name"
    UNIVERSITY = "university"
    DEGREE = "degree"
    BRANCH = "branch"
    EXPECTED_GRADUATION_YEAR = "expected_graduation_year"
    GRADUATION_YEAR = "graduation_year"
    ACADEMIC_YEAR = "academic_year"
    LOCATION = "location"
    SKILL = "skill"
    PROJECT = "project"
    RESEARCH_INTEREST = "research_interest"
    PROFILE_URL = "profile_url"
    PUBLIC_CONTACT = "public_contact"
    AI_ACTIVITY = "ai_activity"
    AI_INTEREST = "ai_interest"


class EvidenceType(StrEnum):
    UNIVERSITY_PROFILE = "university_profile"
    PUBLIC_PROFILE = "public_profile"
    RESUME = "resume"
    PORTFOLIO = "portfolio"
    GITHUB_REPOSITORY = "github_repository"
    PROJECT_PAGE = "project_page"
    PUBLICATION = "publication"
    INTERNSHIP = "internship"
    HACKATHON = "hackathon"
    GITHUB = "github"
    RESEARCH_PROFILE = "research_profile"
    PUBLIC_RESUME = "public_resume"
    PUBLIC_PROJECT_PAGE = "public_project_page"
    EXPLICIT_STATEMENT = "explicit_statement"
    OTHER = "other"


class AIEvidenceCategory(StrEnum):
    AI_PROJECT = "ai_project"
    MACHINE_LEARNING = "machine_learning"
    DEEP_LEARNING = "deep_learning"
    COMPUTER_VISION = "computer_vision"
    NLP = "nlp"
    GENERATIVE_AI = "generative_ai"
    REINFORCEMENT_LEARNING = "reinforcement_learning"
    AI_RESEARCH = "ai_research"
    AI_PUBLICATION = "ai_publication"
    AI_INTERNSHIP = "ai_internship"
    AI_HACKATHON = "ai_hackathon"
    AI_GITHUB = "ai_github"
    AI_PORTFOLIO = "ai_portfolio"
    EXPLICIT_AI_INTEREST = "explicit_ai_interest"
    OTHER_AI_ACTIVITY = "other_ai_activity"


class EvidenceStrength(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ClaimValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"


class FinalYearStatus(StrEnum):
    FINAL_YEAR_VERIFIED = "final_year_verified"
    FINAL_YEAR_UNVERIFIED = "final_year_unverified"
    NON_FINAL_YEAR = "non_final_year"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class AIInterestStatus(StrEnum):
    AI_INTEREST_SUPPORTED = "ai_interest_supported"
    AI_INTEREST_UNVERIFIED = "ai_interest_unverified"
    AI_INTEREST_NOT_FOUND = "ai_interest_not_found"


class SourceReference(BaseModel):
    source_url: HttpUrl
    final_url: HttpUrl | None = None
    title: str | None = None
    evidence_type: EvidenceType = EvidenceType.OTHER
    access: str = "public_or_authorized"


class Evidence(BaseModel):
    evidence_id: str
    claim_type: ClaimType
    claim_value: Any
    evidence_type: EvidenceType
    source_url: HttpUrl
    final_url: HttpUrl | None = None
    source_title: str | None = None
    supporting_text: str = Field(min_length=1)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    validation_status: ClaimValidationStatus = ClaimValidationStatus.UNVALIDATED
    ai_category: AIEvidenceCategory | None = None


class SourcedFact(BaseModel):
    value: Any
    evidence_ids: list[str] = Field(min_length=1)


class StudentProfile(BaseModel):
    student_reference: str | None = None
    name: SourcedFact | None = None
    university: SourcedFact | None = None
    degree: SourcedFact | None = None
    branch: SourcedFact | None = None
    expected_graduation_year: SourcedFact | None = None
    graduation_year: SourcedFact | None = None
    current_academic_year: SourcedFact | None = None
    location: SourcedFact | None = None
    skills: list[SourcedFact] = Field(default_factory=list)
    projects: list[SourcedFact] = Field(default_factory=list)
    research_interests: list[SourcedFact] = Field(default_factory=list)
    github: SourcedFact | None = None
    portfolio: SourcedFact | None = None
    public_contact: SourcedFact | None = None
    final_year_status: FinalYearStatus = FinalYearStatus.FINAL_YEAR_UNVERIFIED
    ai_interest_status: AIInterestStatus = AIInterestStatus.AI_INTEREST_UNVERIFIED
    evidence: list[Evidence] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def all_fact_evidence_must_exist(self) -> "StudentProfile":
        evidence_ids = {item.evidence_id for item in self.evidence}
        facts = [self.name, self.university, self.degree, self.branch,
                 self.expected_graduation_year, self.graduation_year,
                 self.current_academic_year, self.location, self.github,
                 self.portfolio, self.public_contact, *self.skills, *self.projects,
                 *self.research_interests]
        for fact in facts:
            if fact is not None and not set(fact.evidence_ids).issubset(evidence_ids):
                raise ValueError("every sourced fact must reference evidence in the profile")
        return self


class ExtractedStudentInformation(BaseModel):
    profile: StudentProfile = Field(default_factory=StudentProfile)
    evidence: list[Evidence] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    extraction_notes: list[str] = Field(default_factory=list)


class ValidationRequest(BaseModel):
    extracted: ExtractedStudentInformation
    # True only when the caller has completed its intended source research.
    research_complete: bool = False
    current_year: int = Field(default_factory=lambda: datetime.now(timezone.utc).year, ge=2000, le=2200)


class ValidatedClaim(BaseModel):
    claim_type: ClaimType
    value: Any
    status: ClaimValidationStatus
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class ConflictingClaim(BaseModel):
    claim_type: ClaimType
    values: list[Any]
    evidence_ids: list[str]


class StudentValidationResult(BaseModel):
    profile: StudentProfile
    final_year_status: FinalYearStatus
    ai_interest_status: AIInterestStatus
    validated_claims: list[ValidatedClaim] = Field(default_factory=list)
    unsupported_claims: list[ValidatedClaim] = Field(default_factory=list)
    conflicting_claims: list[ConflictingClaim] = Field(default_factory=list)
    evidence_references: list[Evidence] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)
