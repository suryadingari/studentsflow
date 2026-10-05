"""Deterministic, network-free synthetic dataset for the local FYP demo."""

from __future__ import annotations

from app.crawling.models import CrawlMetadata, CrawlResult, SourceAccess, SourceCandidate, SourceType


SYNTHETIC_DEMO_DOMAIN = "synthetic-demo.invalid"
SYNTHETIC_DEMO_URL = f"https://{SYNTHETIC_DEMO_DOMAIN}/dataset/500"
SYNTHETIC_DEMO_TOOL_VERSION = "synthetic-demo-1.0"
SYNTHETIC_DEMO_COUNT = 500

_UNIVERSITIES = (
    "Demo Institute of Technology", "Synthetic National University",
    "Example College of Engineering", "Mock State Technical University",
    "Fictional Institute of Science", "Sample University of India",
    "Demo Polytechnic University", "Synthetic Engineering College",
    "Example Institute of Computing", "Mock University of Technology",
)
_BRANCHES = (
    "Computer Science", "Mechanical Engineering", "Electrical Engineering",
    "Electronics and Communication", "Information Technology", "Civil Engineering",
    "Mathematics and Computing", "Chemical Engineering",
)
_SKILLS = (
    "Python, OpenCV, PyTorch", "Python, SQL, scikit-learn", "C++, Linux, OpenCV",
    "Python, NumPy, TensorFlow", "Java, Python, Hugging Face", "Python, Docker, Keras",
    "C++, OpenCV, NumPy", "Python, Jupyter, PyTorch", "Python, Git, CAD",
    "C++, Linux, MATLAB",
)
_CV_PROJECTS = (
    "Completed synthetic computer vision project using OpenCV and PyTorch",
    "Built synthetic computer vision object detection demo with OpenCV",
    "Implemented synthetic computer vision image segmentation project",
)
_OTHER_AI_PROJECTS = (
    "Completed synthetic natural language processing project using PyTorch",
    "Built synthetic deep learning text classification demo",
    "Implemented synthetic generative AI retrieval demonstration",
)


def build_synthetic_candidate_content(count: int = SYNTHETIC_DEMO_COUNT, *,
                                      current_year: int = 2026) -> str:
    """Generate deterministic, explicitly synthetic profiles as extractor-readable text.

    Distributions: 400 verified, 30 unverified, 50 non-final-year, 20 conflicting;
    350 AI-supported, 75 inconclusive AI mentions, and 75 with no AI evidence.
    The first 300 records include computer-vision evidence for matching demos.
    """
    if count != SYNTHETIC_DEMO_COUNT:
        raise ValueError(f"the FYP demo dataset contains exactly {SYNTHETIC_DEMO_COUNT} records")
    if not 2000 <= current_year <= 2200:
        raise ValueError("current_year must be between 2000 and 2200")

    records = ["SYNTHETIC DEMO DATASET — all records are fictional local demo data."]
    for index in range(count):
        number = index + 1
        marker = f"SYNTHETIC DEMO RECORD ID: {number:04d}"
        lines = [
            f"Name: Synthetic Candidate {number:04d}",
            marker,
            f"University: {_UNIVERSITIES[index % len(_UNIVERSITIES)]}",
            f"Branch: {_BRANCHES[index % len(_BRANCHES)]}",
            "Degree: Synthetic Bachelor of Technology",
            f"Skills: {_SKILLS[index % len(_SKILLS)]}",
            "Location: India",
        ]

        if index < 400:
            lines.append(f"Expected graduation year: {current_year + 1}")
        elif index < 430:
            lines.append("Academic year: Fourth year")
        elif index < 480:
            lines.append(f"Graduation year: {current_year - 1}")
        else:
            lines.extend((f"Expected graduation year: {current_year + 1}",
                          f"Graduation year: {current_year - 1}"))

        if index < 300:
            project = _CV_PROJECTS[index % len(_CV_PROJECTS)]
            lines.append(f"Project: {project} (synthetic reference DEMO-{number:04d})")
            lines.append(f"Research interest: Interested in computer vision (synthetic demo {number:04d})")
        elif index < 350:
            project = _OTHER_AI_PROJECTS[index % len(_OTHER_AI_PROJECTS)]
            lines.append(f"Project: {project} (synthetic reference DEMO-{number:04d})")
        elif index < 425:
            lines.append("Course note: AI concepts were mentioned in a synthetic introductory course")
        else:
            lines.append(f"Project: Synthetic CAD design exercise DEMO-{number:04d}")

        # A few fake .test contacts exercise draft generation and the existing
        # human-approval gate. The configured demo provider never sends mail.
        if number <= 5:
            lines.append(f"Public email: candidate{number:04d}@example.test")
        records.append("\n".join(lines))

    return "\n\n".join(records)


def build_synthetic_demo_crawl_result(*, current_year: int = 2026) -> CrawlResult:
    """Return one mock crawl page containing exactly 500 synthetic records."""
    return CrawlResult(
        requested_url=SYNTHETIC_DEMO_URL,
        final_url=SYNTHETIC_DEMO_URL,
        status_code=200,
        page_title="SYNTHETIC DEMO DATASET — Fictional StudentsFlow Profiles",
        raw_content=build_synthetic_candidate_content(current_year=current_year),
        success=True,
        provenance=CrawlMetadata(
            source_url=SYNTHETIC_DEMO_URL,
            final_url=SYNTHETIC_DEMO_URL,
            domain=SYNTHETIC_DEMO_DOMAIN,
            tool_version=SYNTHETIC_DEMO_TOOL_VERSION,
        ),
    )


def synthetic_demo_source() -> SourceCandidate:
    return SourceCandidate(
        url=SYNTHETIC_DEMO_URL,
        domain=SYNTHETIC_DEMO_DOMAIN,
        source_type=SourceType.SYNTHETIC_DEMO,
        reason="Local synthetic demo fixture; no network request is made",
        access=SourceAccess.AUTHORIZED,
        discovery_metadata={"is_synthetic": True, "dataset_size": SYNTHETIC_DEMO_COUNT},
    )
