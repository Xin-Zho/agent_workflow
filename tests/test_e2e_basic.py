"""End-to-end happy-path test that exercises the full pipeline:

create -> clarify -> search -> screen -> approve -> fetch -> parse ->
extract -> validate -> report -> review -> COMPLETED.

All external dependencies (search, PDF fetch, parse) are mocked via
adapters/mock_* so the test runs deterministically without network.
"""

import os
import sys
import asyncio
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

from workflow_engine import WorkflowStore, TaskStatus, PermissionDeniedError  # noqa: E402
from workflow_config import WorkflowConfig  # noqa: E402
from workflow_models import SearchQuery  # noqa: E402
from adapters.mock_search import MockSearchProvider  # noqa: E402


@pytest.mark.asyncio
async def test_e2e_happy_path():
    """Full pipeline: create -> clarify -> search -> screen -> approve ->
    fetch -> parse -> extract -> validate -> report -> review -> COMPLETED."""
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(
            db_path=os.path.join(tmp, "test.db"),
            worker_id="e2e-worker",
            poll_interval=0.1,
            lease_duration=10,
            renew_interval=2,
        )
        store = WorkflowStore(config.db_path)

        # 1. Alice creates task
        task = store.create_task(
            "alice",
            "Flexible conductive composite optimization",
            "Find optimal PEO/AgNW formulation for high conductivity",
        )

        # 2. Confirm definition
        definition = {
            "research_object": "PEO/AgNW conductive composite",
            "application": "flexible electronics",
            "target_metrics": [
                {"name": "conductivity", "unit": "S/cm", "target_range": ">1000"}
            ],
            "hard_constraints": ["lab feasible", "non-toxic"],
            "optimization_objectives": ["maximize conductivity", "maintain flexibility"],
            "acceptable_tradeoffs": ["cost"],
            "paper_target": 10,
            "languages": ["zh", "en"],
            "temporary_lab_constraints": [],
        }
        store.update_definition(task["id"], "alice", definition)

        # 3. Start search -> creates SEARCHING job
        task = store.start_search(task["id"], "alice")
        assert task["status"] == TaskStatus.SEARCHING

        # 4. Worker: search (mock)
        job = store.claim_next_job(config.worker_id)
        assert job is not None and job["stage"] == "SEARCHING"
        # Simulate mock search results
        search = MockSearchProvider()
        results = await search.search(SearchQuery(text=task["query"]))
        papers = []
        for r in results:
            papers.append(
                {
                    "work_id": r.work_id,
                    "title": r.title,
                    "authors": r.authors,
                    "year": r.year,
                    "abstract": r.abstract,
                    "doi": r.doi,
                    "source": r.source,
                    "document_type": r.document_type,
                    "role_tags": [],
                }
            )
        store.submit_candidates(task["id"], "alice", papers)
        store.complete_job(
            job["id"],
            config.worker_id,
            job["lease_token"],
            result={"papers_found": len(papers)},
        )

        # 5. Task should be WAITING_PAPER_APPROVAL
        task = store.get_task(task["id"], "alice")
        assert task["status"] == TaskStatus.WAITING_PAPER_APPROVAL, (
            f"Expected WAITING_PAPER_APPROVAL, got {task['status']}"
        )

        # 6. Alice selects papers
        all_papers = store.list_papers(task["id"], "alice")
        selected = [p["id"] for p in all_papers[:3]]
        task = store.approve_papers(task["id"], "alice", selected)
        assert task["status"] == TaskStatus.FETCHING_FULLTEXT

        # 7. Worker: fetch fulltext
        job = store.claim_next_job(config.worker_id)
        assert job["stage"] == "FETCHING_FULLTEXT"
        # Mock: mark papers as fetched
        for pid in selected:
            store.update_paper_status(pid, "fetched")
        store.advance(task["id"], "alice", "PARSING")
        store.complete_job(
            job["id"],
            config.worker_id,
            job["lease_token"],
            result={"downloaded": 3},
        )

        # 8. Worker: parse -> reading -> extracting
        for target in ["PARSING", "READING", "EXTRACTING"]:
            job = store.claim_next_job(config.worker_id)
            assert job["stage"] == target
            store.advance(
                task["id"],
                "alice",
                {"PARSING": "READING", "READING": "EXTRACTING", "EXTRACTING": "VALIDATING"}[target],
            )
            store.complete_job(
                job["id"],
                config.worker_id,
                job["lease_token"],
                result={"done": True},
            )

        # 9. Worker: record extractions
        for pid in selected:
            store.record_extraction(
                task["id"],
                "alice",
                pid,
                {
                    "samples": [
                        {
                            "sample_id": "S1",
                            "ratios": [
                                {"component": "AgNW", "raw_value": "25", "raw_unit": "wt%"}
                            ],
                        }
                    ]
                },
                "explicit",
                85.0,
            )
            store.update_paper_status(pid, "extracted")

        # 10. Worker: validate -> generate report
        for target in ["VALIDATING", "GENERATING_REPORT"]:
            job = store.claim_next_job(config.worker_id)
            assert job["stage"] == target
            if target == "GENERATING_REPORT":
                store.request_data_review(task["id"], "alice")
            else:
                store.advance(task["id"], "alice", "GENERATING_REPORT")
            store.complete_job(
                job["id"],
                config.worker_id,
                job["lease_token"],
                result={"done": True},
            )

        # 11. Task should be WAITING_DATA_REVIEW
        task = store.get_task(task["id"], "alice")
        assert task["status"] == TaskStatus.WAITING_DATA_REVIEW

        # 12. Admin approves
        task = store.review_extractions(
            task["id"], "admin", approved=True, is_admin=True
        )
        assert task["status"] == TaskStatus.COMPLETED

        # 13. Verify events exist
        events = store.events(task["id"], "alice")
        event_types = [e["event_type"] for e in events]
        assert "task_created" in event_types
        assert "data_reviewed" in event_types

    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_e2e_user_isolation():
    """Alice creates task; Bob cannot access it."""
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        alice_task = store.create_task("alice", "alice secret", "private research")
        # Bob cannot read
        with pytest.raises(PermissionDeniedError):
            store.get_task(alice_task["id"], "bob")
        # Bob's list is empty
        assert store.list_tasks("bob") == []
        # Admin can read
        task = store.get_task(alice_task["id"], "admin", is_admin=True)
        assert task["title"] == "alice secret"
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_e2e_human_gates_owner_and_admin():
    """Owner AND admin can approve papers and review extractions."""
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        # Setup: task at WAITING_PAPER_APPROVAL
        task = store.create_task("alice", "test", "query")
        store.update_definition(
            task["id"],
            "alice",
            {
                "research_object": "x",
                "application": "y",
                "target_metrics": [],
                "hard_constraints": [],
                "optimization_objectives": [],
                "acceptable_tradeoffs": [],
            },
        )
        store.start_search(task["id"], "alice")
        store.submit_candidates(
            task["id"],
            "alice",
            [
                {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
            ],
        )
        # Admin approves
        task = store.approve_papers(task["id"], "admin", ["p1"], is_admin=True)
        assert task["status"] == TaskStatus.FETCHING_FULLTEXT
        # Non-owner, non-admin cannot
        task2 = store.create_task("alice", "test2", "query2")
        store.update_definition(
            task2["id"],
            "alice",
            {
                "research_object": "x",
                "application": "y",
                "target_metrics": [],
                "hard_constraints": [],
                "optimization_objectives": [],
                "acceptable_tradeoffs": [],
            },
        )
        store.start_search(task2["id"], "alice")
        store.submit_candidates(
            task2["id"],
            "alice",
            [
                {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
            ],
        )
        with pytest.raises(PermissionDeniedError):
            store.approve_papers(task2["id"], "bob", ["p1"])
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_e2e_no_premature_job():
    """After candidates submitted, no FETCHING_FULLTEXT job exists until approval."""
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.update_definition(
            task["id"],
            "alice",
            {
                "research_object": "x",
                "application": "y",
                "target_metrics": [],
                "hard_constraints": [],
                "optimization_objectives": [],
                "acceptable_tradeoffs": [],
            },
        )
        store.start_search(task["id"], "alice")

        # Worker claims the SEARCHING job and completes it (no FETCHING_FULLTEXT yet)
        search_job = store.claim_next_job(config.worker_id)
        assert search_job is not None and search_job["stage"] == "SEARCHING"

        # Submit candidates (task transitions to WAITING_PAPER_APPROVAL)
        store.submit_candidates(
            task["id"],
            "alice",
            [
                {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
            ],
        )
        store.complete_job(
            search_job["id"],
            config.worker_id,
            search_job["lease_token"],
            result={"papers_found": 1},
        )

        # Verify no FETCHING_FULLTEXT job exists during WAITING_PAPER_APPROVAL
        next_job = store.claim_next_job(config.worker_id)
        assert next_job is None, "No job should exist during WAITING_PAPER_APPROVAL"
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_e2e_single_paper_degradation():
    """3 papers: 1 OK, 1 PDF corrupt, 1 partial parse. Task continues."""
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.update_definition(
            task["id"],
            "alice",
            {
                "research_object": "x",
                "application": "y",
                "target_metrics": [],
                "hard_constraints": [],
                "optimization_objectives": [],
                "acceptable_tradeoffs": [],
            },
        )
        store.start_search(task["id"], "alice")
        store.submit_candidates(
            task["id"],
            "alice",
            [
                {"id": "p1", "title": "Good paper", "role_tags": ["target_performance"]},
                {"id": "p2", "title": "Corrupt PDF", "role_tags": ["lab_process"]},
                {"id": "p3", "title": "Partial parse", "role_tags": ["structure"]},
            ],
        )
        task = store.approve_papers(task["id"], "alice", ["p1", "p2", "p3"])

        # Simulate Worker: p1 fetched OK, p2 PDF unavailable, p3 fetched but parse error
        store.update_paper_status("p1", "fetched")
        store.update_paper_status("p2", "degraded", error="PDF unavailable")
        store.update_paper_status("p3", "fetched")
        store.update_paper_status("p3", "degraded", error="parse error: missing pages")

        papers = store.list_papers(task["id"], "alice")
        statuses = {p["id"]: p["paper_status"] for p in papers}
        assert statuses["p1"] == "fetched"
        assert statuses["p2"] == "degraded"
        assert statuses["p3"] == "degraded"
        # Task should still be running (not FAILED)
        task = store.get_task(task["id"], "alice")
        assert task["status"] not in {TaskStatus.FAILED}
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_e2e_lease_fencing():
    """Old worker cannot overwrite new worker's result after lease loss."""
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.start_search(task["id"], "alice")
        # Worker 1 claims
        job = store.claim_next_job("worker-1", lease_duration=1)
        assert job is not None
        # Manually change lease_token (simulating Worker 2 re-claim after expiry)
        with store._connect() as conn:
            conn.execute(
                "UPDATE jobs SET lease_token = 'new-token', worker_id = 'worker-2' WHERE id = ?",
                (job["id"],),
            )
        # Worker 1 tries to complete with old token — must fail
        ok = store.complete_job(job["id"], "worker-1", job["lease_token"], result={})
        assert ok is False, "Old worker should be fenced"
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
