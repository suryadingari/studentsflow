"""Full local-demo coverage for the deterministic synthetic dataset."""

import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.workflows import router as workflows_router
from app.auth.security import Principal, current_principal
from app.core.config import settings
from app.crawling.demo_fixture import (
    SYNTHETIC_DEMO_COUNT,
    SYNTHETIC_DEMO_DOMAIN,
    build_synthetic_candidate_content,
    build_synthetic_demo_crawl_result,
)
from app.db.base import Base
from app.models import StudentEvidenceRecord, StudentRecord, StudentSourceRecord
from app.orchestration import create_demo_orchestrator
from app.schemas.email import ApprovalAction, ApprovalActionType
from app.schemas.matching import CriterionType, UserCriterion
from app.schemas.workflow import WorkflowStatus
from app.outreach.provider import MockEmailProvider


def test_synthetic_dataset_has_exactly_500_marked_profiles_and_safe_fake_references():
    content = build_synthetic_candidate_content()
    names = [line.removeprefix("Name: ") for line in content.splitlines()
             if line.startswith("Name: ")]

    assert len(names) == SYNTHETIC_DEMO_COUNT
    assert len(set(names)) == SYNTHETIC_DEMO_COUNT
    assert names[0] == "Synthetic Candidate 0001"
    assert names[-1] == "Synthetic Candidate 0500"
    assert content.count("SYNTHETIC DEMO RECORD ID:") == SYNTHETIC_DEMO_COUNT
    assert "linkedin.com" not in content.lower()
    assert "github.com" not in content.lower()
    assert "candidate0001@example.test" in content
    assert all("@example.test" in line for line in content.splitlines()
               if "Public email:" in line)
    assert build_synthetic_demo_crawl_result().provenance.tool_version == "synthetic-demo-1.0"


def test_production_demo_workflow_persists_all_500_candidates_and_honors_approval(tmp_path, monkeypatch):
    # Verify the explicit synthetic path works outside local demo mode while
    # production authentication remains intact (the test supplies its principal).
    monkeypatch.setattr(settings, "app_env", "production")
    database_url = "sqlite+aiosqlite:///" + (tmp_path / "synthetic_demo.sqlite3").as_posix()

    async def exercise():
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            orchestrator = create_demo_orchestrator(
                sessions=sessions,
                database_url=database_url,
                allowed_domains={SYNTHETIC_DEMO_DOMAIN},
                include_synthetic_demo=True,
                allow_real_email=False,
            )
            api_app = FastAPI()
            api_app.include_router(workflows_router)
            api_app.state.workflow_orchestrator = orchestrator
            api_app.dependency_overrides[current_principal] = lambda: Principal(
                user_id="demo-admin", username="demo-admin", role="admin")

            payload = {
                "requirement": {
                    "raw_text": "Final-year AI students with computer vision experience",
                    "criteria": [
                        {"criterion_type": "final_year", "required": True},
                        {"criterion_type": "ai_interest", "required": True},
                        {"criterion_type": "ai_area", "value": "Computer Vision", "required": True},
                    ],
                },
                "synthetic_demo_dataset": True,
                "demo_mode": True,
                "sources": [],
                "search_enabled": True,
                "permitted_domains": [],
                "crawl_configuration": {"allowed_domains": [], "max_pages": 5, "max_depth": 1},
            }

            with TestClient(api_app) as client:
                created = client.post("/workflows", json=payload)
                assert created.status_code == 201, created.text
                workflow_id = created.json()["workflow_id"]

                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    current = orchestrator.store.get(workflow_id)
                    if current.status not in {WorkflowStatus.CREATED, WorkflowStatus.RUNNING}:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("Synthetic demo workflow did not finish within three minutes")

                waiting = orchestrator.store.get(workflow_id)
                assert waiting.status == WorkflowStatus.WAITING_FOR_APPROVAL, waiting.errors
                assert waiting.synthetic_demo_dataset
                assert len(waiting.extracted_profiles) == SYNTHETIC_DEMO_COUNT
                assert len(waiting.validated_profiles) == SYNTHETIC_DEMO_COUNT
                assert len(waiting.canonical_students) == SYNTHETIC_DEMO_COUNT
                assert len(waiting.candidate_matches) == SYNTHETIC_DEMO_COUNT
                assert waiting.metrics.candidates_matched == 300
                assert len(waiting.drafts) == 5
                assert all(draft.recipient.email.endswith("@example.test") for draft in waiting.drafts)

                final_statuses = {item.final_year_status.value for item in waiting.validated_profiles}
                ai_statuses = {item.ai_interest_status.value for item in waiting.validated_profiles}
                assert final_statuses == {
                    "final_year_verified", "final_year_unverified", "non_final_year",
                    "conflicting_evidence",
                }
                assert ai_statuses == {
                    "ai_interest_supported", "ai_interest_unverified", "ai_interest_not_found",
                }
                assert all(item.profile.is_synthetic
                           and item.profile.source_type == "synthetic_demo"
                           for item in waiting.validated_profiles)
                assert all(str(evidence.source_url).startswith(f"https://{SYNTHETIC_DEMO_DOMAIN}/candidates/")
                           for item in waiting.validated_profiles
                           for evidence in item.evidence_references)
                assert all(str(source.source_url).startswith(
                    f"https://{SYNTHETIC_DEMO_DOMAIN}/candidates/")
                    for item in waiting.validated_profiles
                    for source in item.profile.source_references)

                # The actual candidate listing endpoint returns the persisted workflow results.
                candidates_response = client.get("/workflows?limit=10")
                assert candidates_response.status_code == 200
                candidate_workflow = next(item for item in candidates_response.json()
                                          if item["workflow_id"] == workflow_id)
                assert candidate_workflow["synthetic_demo_dataset"] is True
                assert len(candidate_workflow["candidate_matches"]) == SYNTHETIC_DEMO_COUNT
                assert sum(bool(candidate["profile"]["is_synthetic"])
                           for candidate in candidate_workflow["canonical_students"]) == SYNTHETIC_DEMO_COUNT

                metrics = client.get("/workflows/observability/kpis")
                assert metrics.status_code == 200
                assert metrics.json()["candidates_discovered"] == SYNTHETIC_DEMO_COUNT

                async with sessions() as session:
                    assert await session.scalar(select(func.count()).select_from(StudentRecord).where(
                        StudentRecord.workflow_id == workflow_id)) == SYNTHETIC_DEMO_COUNT
                    assert await session.scalar(select(func.count()).select_from(StudentSourceRecord).join(
                        StudentRecord, StudentSourceRecord.student_id == StudentRecord.student_id).where(
                            StudentRecord.workflow_id == workflow_id)) == SYNTHETIC_DEMO_COUNT
                    assert await session.scalar(select(func.count()).select_from(StudentEvidenceRecord).join(
                        StudentRecord, StudentEvidenceRecord.student_id == StudentRecord.student_id).where(
                            StudentRecord.workflow_id == workflow_id)) > SYNTHETIC_DEMO_COUNT

                provider = orchestrator.registry.get("outreach").demo_provider
                assert isinstance(provider, MockEmailProvider)
                assert provider.calls == []
                for draft in waiting.drafts:
                    completed = await orchestrator.resume_after_approval(
                        workflow_id, draft.draft_id,
                        ApprovalAction(action=ApprovalActionType.APPROVE,
                                       actor_id="synthetic-demo-reviewer"),
                    )
                assert completed.status == WorkflowStatus.COMPLETED
                assert len(provider.calls) == 5
                assert all(call.recipient.endswith("@example.test") for call in provider.calls)
                restored = await orchestrator.get(workflow_id)
                assert restored.status == WorkflowStatus.COMPLETED
                assert len(restored.candidate_matches) == SYNTHETIC_DEMO_COUNT
        finally:
            await engine.dispose()

    asyncio.run(exercise())
