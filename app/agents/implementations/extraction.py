"""Deterministic, provenance-preserving extraction from a single CrawlResult."""

import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from app.agents.base import BaseAgent
from app.agents.context import AgentContext
from app.agents.models import AgentInput
from app.crawling.models import CrawlResult
from app.crawling.demo_fixture import SYNTHETIC_DEMO_DOMAIN, SYNTHETIC_DEMO_TOOL_VERSION
from app.schemas.student import (
    AIEvidenceCategory, ClaimType, Evidence, EvidenceStrength, EvidenceType,
    ExtractedStudentInformation, FinalYearStatus, SourceReference, SourcedFact,
    StudentProfile,
)


_AI_PATTERNS: tuple[tuple[re.Pattern[str], AIEvidenceCategory], ...] = (
    (re.compile(r"computer\s+vision|\bcv\b", re.I), AIEvidenceCategory.COMPUTER_VISION),
    (re.compile(r"deep\s+learning", re.I), AIEvidenceCategory.DEEP_LEARNING),
    (re.compile(r"machine\s+learning|\bml\b", re.I), AIEvidenceCategory.MACHINE_LEARNING),
    (re.compile(r"natural\s+language\s+processing|\bnlp\b", re.I), AIEvidenceCategory.NLP),
    (re.compile(r"generative\s+ai|\bgenai\b|large\s+language\s+model", re.I), AIEvidenceCategory.GENERATIVE_AI),
    (re.compile(r"artificial\s+intelligence|\bAI\b", re.I), AIEvidenceCategory.OTHER_AI_ACTIVITY),
)
_ACTIVITY = re.compile(r"project|built|developed|research|publication|internship|hackathon|repository|portfolio|experience|worked on", re.I)
_INTEREST = re.compile(r"interested in|interest in|passionate about|research interests?", re.I)


class _VisibleText(HTMLParser):
    """Extract visible text with line boundaries from public HTML pages."""

    _BLOCKS = {"address", "article", "br", "dd", "div", "dt", "h1", "h2", "h3", "h4",
               "li", "p", "section", "tr"}
    _IGNORED = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._IGNORED:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self.ignored_depth:
            self.ignored_depth -= 1
        elif not self.ignored_depth and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(data.strip())


def _readable_text(content: str) -> str:
    if "<" not in content or ">" not in content:
        return content
    parser = _VisibleText()
    try:
        parser.feed(content)
        return "\n".join(part for part in parser.parts if part.strip())
    except Exception:
        # Preserve source text rather than dropping evidence on malformed HTML.
        return content


def _source_type(url: str, title: str | None) -> EvidenceType:
    host = urlparse(url).hostname or ""
    if "github.com" in host:
        return EvidenceType.GITHUB_REPOSITORY
    if title and "university" in title.lower():
        return EvidenceType.UNIVERSITY_PROFILE
    return EvidenceType.PUBLIC_PROFILE


class ExtractionAgent(BaseAgent):
    agent_name = "extraction"
    agent_version = "2.0.0"
    description = "Extracts evidence-linked claims from crawled public or authorized content."
    allowed_tools = frozenset()

    async def _execute(self, agent_input: AgentInput, context: AgentContext) -> ExtractedStudentInformation:
        crawl = agent_input.payload
        if not isinstance(crawl, CrawlResult):
            crawl = CrawlResult.model_validate(crawl)
        readable = _readable_text(crawl.raw_content or "")
        synthetic_demo = crawl.provenance.tool_version == SYNTHETIC_DEMO_TOOL_VERSION
        candidate_sections = _split_named_candidate_sections(readable)
        if len(candidate_sections) > 1:
            records = []
            for section in candidate_sections:
                updates = {"raw_content": section}
                if synthetic_demo:
                    record_id = _synthetic_record_id(section)
                    if record_id is None:
                        continue
                    source_url = f"https://{SYNTHETIC_DEMO_DOMAIN}/candidates/{record_id}"
                    provenance = crawl.provenance.model_copy(update={
                        "source_url": source_url, "final_url": source_url,
                        "domain": SYNTHETIC_DEMO_DOMAIN,
                    }, deep=True)
                    updates.update(requested_url=source_url, final_url=source_url,
                                   provenance=provenance)
                child = crawl.model_copy(update=updates, deep=True)
                records.append(await self._execute(AgentInput(payload=child), context))
            if not records:
                return ExtractedStudentInformation(extraction_notes=[
                    "Synthetic demo page contained no valid candidate record identifiers."])
            primary = records[0]
            return primary.model_copy(update={"additional_candidates": records[1:]}, deep=True)
        source_url = crawl.requested_url
        source_type = (EvidenceType.SYNTHETIC_DEMO if synthetic_demo
                       else _source_type(source_url, crawl.page_title))
        record_id = _synthetic_record_id(readable) if synthetic_demo else None
        source = SourceReference(
            source_url=source_url,
            final_url=crawl.final_url,
            title=crawl.page_title,
            evidence_type=source_type,
            source_type="synthetic_demo" if synthetic_demo else "public_or_authorized",
            is_synthetic=synthetic_demo,
            access="authorized" if synthetic_demo else "public_or_authorized",
        )
        if not crawl.success or not crawl.raw_content:
            return ExtractedStudentInformation(
                source_references=[source],
                extraction_notes=["Source content was unavailable; no student facts were extracted."],
            )

        evidence: list[Evidence] = []
        facts: dict[ClaimType, list[SourcedFact]] = {}

        def add(claim: ClaimType, value: Any, snippet: str, *, kind: EvidenceType = source_type,
                category: AIEvidenceCategory | None = None, strength: EvidenceStrength = EvidenceStrength.MEDIUM) -> None:
            ev_id = hashlib.sha256(f"{source_url}\0{claim.value}\0{value}\0{snippet}".encode()).hexdigest()[:20]
            if any(item.evidence_id == ev_id for item in evidence):
                return
            item = Evidence(evidence_id=ev_id, claim_type=claim, claim_value=value,
                            evidence_type=kind, source_url=source_url, final_url=crawl.final_url,
                            source_title=crawl.page_title, supporting_text=snippet.strip(),
                            strength=strength, ai_category=category)
            evidence.append(item)
            facts.setdefault(claim, []).append(SourcedFact(value=value, evidence_ids=[ev_id]))

        labels = {
            "name": ClaimType.NAME, "university": ClaimType.UNIVERSITY, "institution": ClaimType.UNIVERSITY,
            "degree": ClaimType.DEGREE, "branch": ClaimType.BRANCH, "major": ClaimType.BRANCH,
            "expected graduation": ClaimType.EXPECTED_GRADUATION_YEAR,
            "expected graduation year": ClaimType.EXPECTED_GRADUATION_YEAR,
            "graduation year": ClaimType.GRADUATION_YEAR, "graduated": ClaimType.GRADUATION_YEAR,
            "academic year": ClaimType.ACADEMIC_YEAR, "current year": ClaimType.ACADEMIC_YEAR,
            "current academic year": ClaimType.ACADEMIC_YEAR,
            "location": ClaimType.LOCATION, "skill": ClaimType.SKILL, "skills": ClaimType.SKILL,
            "public contact": ClaimType.PUBLIC_CONTACT, "public email": ClaimType.PUBLIC_CONTACT,
            "github": ClaimType.PROFILE_URL, "github url": ClaimType.PROFILE_URL,
            "portfolio": ClaimType.PROFILE_URL, "portfolio url": ClaimType.PROFILE_URL,
            "project": ClaimType.PROJECT, "projects": ClaimType.PROJECT,
            "research interest": ClaimType.RESEARCH_INTEREST, "research interests": ClaimType.RESEARCH_INTEREST,
        }
        for line in readable.splitlines():
            text = line.strip().lstrip("#*- ").strip()
            if not text:
                continue
            match = re.match(r"([A-Za-z ]+):\s*(.+)$", text)
            if match:
                label, value = match.group(1).strip().lower(), match.group(2).strip()
                claim = labels.get(label)
                if claim:
                    if claim in {ClaimType.EXPECTED_GRADUATION_YEAR, ClaimType.GRADUATION_YEAR}:
                        year_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", value)
                        if year_match:
                            add(claim, int(year_match.group()), text, strength=EvidenceStrength.HIGH)
                    elif claim == ClaimType.ACADEMIC_YEAR:
                        add(claim, value, text, strength=EvidenceStrength.HIGH)
                    elif claim in {ClaimType.SKILL, ClaimType.PROJECT, ClaimType.RESEARCH_INTEREST}:
                        values = re.split(r",\s*", value) if claim == ClaimType.SKILL else [value]
                        for val in values:
                            add(claim, val, text)
                    else:
                        add(claim, value, text, strength=EvidenceStrength.HIGH)

            # Explicit textual academic-year statements are evidence even without labels.
            if re.search(r"\b(final[- ]year|final year student)\b", text, re.I):
                add(ClaimType.ACADEMIC_YEAR, "final year", text, strength=EvidenceStrength.HIGH)
            for match_year in re.finditer(r"(?:expected to graduate|expected graduation(?: year)?|graduation year)\D{0,20}(19\d{2}|20\d{2}|21\d{2})", text, re.I):
                claim = ClaimType.EXPECTED_GRADUATION_YEAR if "expected" in match_year.group(0).lower() else ClaimType.GRADUATION_YEAR
                add(claim, int(match_year.group(1)), text, strength=EvidenceStrength.HIGH)

            ai_matches = [(pattern, category) for pattern, category in _AI_PATTERNS if pattern.search(text)]
            if ai_matches:
                if _ACTIVITY.search(text):
                    for _, category in ai_matches:
                        ai_claim = ClaimType.AI_ACTIVITY
                        kind = EvidenceType.PROJECT_PAGE if "project" in text.lower() else source_type
                        add(ai_claim, text, text, kind=kind, category=category, strength=EvidenceStrength.HIGH)
                    # A project is separately preserved as a candidate fact, with the same source.
                    if not any(f.value == text for f in facts.get(ClaimType.PROJECT, [])):
                        add(ClaimType.PROJECT, text, text, kind=EvidenceType.PROJECT_PAGE)
                elif _INTEREST.search(text):
                    add(ClaimType.AI_INTEREST, text, text, kind=EvidenceType.EXPLICIT_STATEMENT,
                        category=AIEvidenceCategory.EXPLICIT_AI_INTEREST, strength=EvidenceStrength.HIGH)
                else:
                    # A keyword mention is retained for validation as an uncertain indicator.
                    add(ClaimType.AI_INTEREST, text, text, category=ai_matches[0][1], strength=EvidenceStrength.LOW)

        links = crawl.discovered_links
        for link in links:
            host = urlparse(link).hostname or ""
            if "github.com" in host:
                add(ClaimType.PROFILE_URL, link, link, kind=EvidenceType.GITHUB_REPOSITORY)
            elif any(token in (urlparse(link).path or "").lower() for token in ("portfolio", "projects")):
                add(ClaimType.PROFILE_URL, link, link, kind=EvidenceType.PORTFOLIO)

        def one(key: ClaimType) -> SourcedFact | None:
            values = facts.get(key, [])
            return values[0] if values else None

        profile = StudentProfile(
            student_reference=f"synthetic-candidate-{record_id}" if record_id else None,
            source_type="synthetic_demo" if synthetic_demo else "public_or_authorized",
            is_synthetic=synthetic_demo,
            name=one(ClaimType.NAME), university=one(ClaimType.UNIVERSITY), degree=one(ClaimType.DEGREE),
            branch=one(ClaimType.BRANCH), expected_graduation_year=one(ClaimType.EXPECTED_GRADUATION_YEAR),
            graduation_year=one(ClaimType.GRADUATION_YEAR), current_academic_year=one(ClaimType.ACADEMIC_YEAR),
            location=one(ClaimType.LOCATION), skills=facts.get(ClaimType.SKILL, []),
            projects=facts.get(ClaimType.PROJECT, []), research_interests=facts.get(ClaimType.RESEARCH_INTEREST, []),
            github=next((fact for fact in facts.get(ClaimType.PROFILE_URL, []) if "github.com" in str(fact.value)), None),
            portfolio=next((fact for fact in facts.get(ClaimType.PROFILE_URL, []) if "github.com" not in str(fact.value)), None),
            public_contact=one(ClaimType.PUBLIC_CONTACT),
            evidence=evidence, source_references=[source],
        )
        note = [] if evidence else ["No structured student information was identified in the source content."]
        return ExtractedStudentInformation(profile=profile, evidence=evidence,
                                           source_references=[source], extraction_notes=note)


def _split_named_candidate_sections(content: str) -> list[str]:
    """Separate clearly labeled profile sections without guessing page structure."""
    lines = content.splitlines()
    starts = [index for index, line in enumerate(lines)
              if re.match(r"\s*(?:[-*]\s*)?name\s*:\s*\S+", line, re.I)]
    if len(starts) < 2:
        return [content]
    return ["\n".join(lines[start:end]).strip()
            for start, end in zip(starts, [*starts[1:], len(lines)])]


def _synthetic_record_id(content: str) -> str | None:
    match = re.search(r"SYNTHETIC DEMO RECORD ID:\s*(\d{4})\b", content, re.I)
    return match.group(1) if match else None
