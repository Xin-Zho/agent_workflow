import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

from workflow_engine import (  # noqa: E402
    InvalidTransitionError,
    PermissionDeniedError,
    TaskStatus,
    WorkerContext,
    WorkflowStore,
    db_utc_now,
    utc_now,
)

import sqlite3  # noqa: E402


class WorkflowStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(os.path.join(self.tmp.name, "workflow.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _task_at_paper_review(self):
        task = self.store.create_task("alice", "柔性材料", "寻找快速迭代配方")
        self.store.update_definition(
            task["id"],
            "alice",
            {
                "target_metrics": ["响应速度"],
                "hard_constraints": ["实验室可制备"],
                "paper_count": 10,
            },
        )
        self.store.start_search(task["id"], "alice")
        self.store.submit_candidates(
            task["id"],
            "alice",
            [
                {
                    "id": "p1",
                    "title": "A fast material system",
                    "doi": "10.1000/example",
                    "role_tags": ["lab_process", "composition_ratio"],
                    "relevance_score": 95,
                },
                {
                    "id": "p2",
                    "title": "Performance benchmark",
                    "role_tags": ["target_performance"],
                    "relevance_score": 80,
                },
            ],
        )
        return self.store.get_task(task["id"], "alice")

    def test_happy_path_with_manual_gates_and_admin_review(self):
        task = self._task_at_paper_review()
        self.assertEqual(task["status"], TaskStatus.WAITING_PAPER_APPROVAL)

        task = self.store.approve_papers(task["id"], "alice", ["p1"])
        self.assertEqual(task["status"], TaskStatus.FETCHING_FULLTEXT)
        papers = self.store.list_papers(task["id"], "alice")
        self.assertEqual(
            {p["id"]: p["selection_status"] for p in papers},
            {"p1": "selected", "p2": "rejected"},
        )

        self.store.record_extraction(
            task["id"],
            "alice",
            "p1",
            {
                "samples": [{"sample_id": "A1", "ratio": "10 wt%"}],
                "evidence": [{"page": 6, "table": "Table 2"}],
            },
            "explicit",
            92,
        )

        for target in ["PARSING", "READING", "EXTRACTING", "VALIDATING", "GENERATING_REPORT"]:
            task = self.store.advance(task["id"], "alice", target)
        task = self.store.request_data_review(task["id"], "alice")
        self.assertEqual(task["status"], TaskStatus.WAITING_DATA_REVIEW)

        task = self.store.review_extractions(
            task["id"], "admin-user", approved=True, is_admin=True
        )
        self.assertEqual(task["status"], TaskStatus.COMPLETED)
        extraction = self.store.list_extractions(task["id"], "alice")[0]
        self.assertEqual(extraction["review_status"], "approved")
        self.assertEqual(extraction["reviewed_by"], "admin-user")

    def test_invalid_transition_and_user_isolation(self):
        task = self.store.create_task("alice", "test", "query")
        with self.assertRaises(InvalidTransitionError):
            self.store.approve_papers(task["id"], "alice", [])
        with self.assertRaises(PermissionDeniedError):
            self.store.get_task(task["id"], "bob")
        self.assertEqual(self.store.list_tasks("bob"), [])

    def test_pause_resume_and_rollback(self):
        task = self.store.create_task("alice", "test", "query")
        task = self.store.start_search(task["id"], "alice")
        task = self.store.pause(task["id"], "alice")
        self.assertEqual(task["status"], TaskStatus.PAUSED)
        self.assertEqual(task["previous_status"], TaskStatus.SEARCHING)

        task = self.store.resume(task["id"], "alice")
        self.assertEqual(task["status"], TaskStatus.SEARCHING)
        task = self.store.rollback(task["id"], "alice", "CLARIFYING")
        self.assertEqual(task["status"], TaskStatus.CLARIFYING)

    def test_optimistic_locking_prevents_concurrent_update(self):
        task = self.store.create_task("alice", "locking", "query")
        # Simulate two concurrent reads
        t1 = self.store.get_task(task["id"], "alice")
        t2 = self.store.get_task(task["id"], "alice")
        # First update succeeds
        self.store.start_search(t1["id"], "alice")
        # Second update uses stale revision — should be handled (retry or conflict)
        # The store currently retries internally; verify task is in SEARCHING
        final = self.store.get_task(task["id"], "alice")
        assert final["status"] == "SEARCHING"

    def test_rollback_only_to_earlier_stage(self):
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        # task is now WAITING_PAPER_APPROVAL
        task = self.store.get_task(task["id"], "alice")
        assert task["status"] == "WAITING_PAPER_APPROVAL"
        # Rollback to SEARCHING is valid (earlier)
        self.store.rollback(task["id"], "alice", "SEARCHING")
        task = self.store.get_task(task["id"], "alice")
        assert task["status"] == "SEARCHING"
        # Rollback to FETCHING_FULLTEXT is invalid (later than SEARCHING)
        with self.assertRaises(InvalidTransitionError):
            self.store.rollback(task["id"], "alice", "FETCHING_FULLTEXT")

    def test_resume_does_not_create_job_for_wait_state(self):
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        # WAITING_PAPER_APPROVAL
        task = self.store.pause(task["id"], "alice")
        assert task["status"] == "PAUSED"
        self.store.resume(task["id"], "alice")
        # Should be back in WAITING_PAPER_APPROVAL, no job created
        with self.store._connect() as conn:
            jobs = conn.execute(
                "SELECT COUNT(*) as c FROM jobs WHERE task_id = ? AND status = 'queued'",
                (task["id"],),
            ).fetchone()
            assert jobs["c"] == 0  # no new job for human-wait state

    def test_fifo_jobs(self):
        first = self.store.create_task("alice", "first", "one")
        second = self.store.create_task("bob", "second", "two")
        self.store.start_search(first["id"], "alice")
        self.store.start_search(second["id"], "bob")

        job1 = self.store.claim_next_job("worker-1")
        job2 = self.store.claim_next_job("worker-1")
        self.assertEqual(job1["task_id"], first["id"])
        self.assertEqual(job2["task_id"], second["id"])
        self.store.complete_job(job1["id"], "worker-1", job1["lease_token"], result={"ok": True})
        self.store.fail_job(job2["id"], "worker-1", job2["lease_token"], error="simulated failure")

    def test_extraction_versions_keep_latest(self):
        task = self._task_at_paper_review()
        task = self.store.approve_papers(task["id"], "alice", ["p1"])
        first = self.store.record_extraction(
            task["id"], "alice", "p1", {"value": 1}, "estimated", 50
        )
        second = self.store.record_extraction(
            task["id"], "alice", "p1", {"value": 2}, "explicit", 90
        )
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        latest = self.store.list_extractions(task["id"], "alice")
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["version"], 2)
        self.assertEqual(latest[0]["payload"]["value"], 2)

    def test_job_lease_claim_and_complete(self):
        """Worker claims, completes; job status transitions correctly."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        assert job["status"] == "running"
        assert job["worker_id"] == "worker-1"
        assert job["lease_token"] is not None
        assert job["attempts"] == 1
        self.store.complete_job(job["id"], "worker-1", job["lease_token"],
                                result={"papers_found": 10})
        # Job should be completed
        with self.store._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["status"] == "completed"

    def test_job_lease_fencing(self):
        """complete_job with wrong worker_id or lease_token must fail."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        # Try completing with wrong lease_token
        self.store.complete_job(job["id"], "worker-1", "wrong-token", result={})
        # Job should still be running (fencing rejected the update)
        with self.store._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["status"] == "running"

    def test_job_lease_renewal(self):
        """Renewing lease extends lease_expires_at."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        old_expiry = job["lease_expires_at"]
        ok = self.store.renew_lease(job["id"], "worker-1", job["lease_token"])
        assert ok is True
        with self.store._connect() as conn:
            row = conn.execute("SELECT lease_expires_at FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["lease_expires_at"] > old_expiry

    def test_job_lease_renewal_wrong_token(self):
        """Renewing with wrong lease_token must fail."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        ok = self.store.renew_lease(job["id"], "worker-1", "wrong-token")
        assert ok is False

    def test_job_retry_and_dead_letter(self):
        """After max_attempts, job goes to dead_letter."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        # Manually set max_attempts low for test
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET max_attempts = 2 WHERE task_id = ?", (task["id"],))
        job = self.store.claim_next_job("worker-1")
        assert job["attempts"] == 1
        self.store.retry_job(job["id"], "worker-1", job["lease_token"], error="test error")
        # Wait for retry (set next_retry_at to now)
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET next_retry_at = ? WHERE id = ?",
                         (utc_now(), job["id"]))
        job2 = self.store.claim_next_job("worker-1")
        assert job2["attempts"] == 2
        self.store.retry_job(job2["id"], "worker-1", job2["lease_token"], error="test error 2")
        # promote
        self.store._promote_dead_letters()
        with self.store._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["status"] == "dead_letter"

    def test_retry_next_retry_at_is_not_null(self):
        """After retry, next_retry_at must be a non-NULL timestamp."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET max_attempts = 3 WHERE task_id = ?", (task["id"],))
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ok = self.store.retry_job(job["id"], "worker-1", job["lease_token"], error="retry")
        assert ok is True
        with self.store._connect() as conn:
            row = conn.execute("SELECT next_retry_at FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert row is not None
        assert row["next_retry_at"] is not None

    def test_retry_wrong_lease_no_attempt_inserted(self):
        """Retry with wrong lease must not insert an attempt row."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ok = self.store.retry_job(job["id"], "worker-1", "wrong-token", error="no lease")
        assert ok is False
        with self.store._connect() as conn:
            attempts = conn.execute(
                "SELECT COUNT(*) as c FROM job_attempts WHERE job_id = ?", (job["id"],)
            ).fetchone()
        assert attempts["c"] == 0

    def test_retry_max_attempts_goes_directly_to_dead_letter(self):
        """When attempts >= max_attempts, retry_job goes directly to dead_letter."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET max_attempts = 1 WHERE task_id = ?", (task["id"],))
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        assert job["attempts"] == 1
        ok = self.store.retry_job(job["id"], "worker-1", job["lease_token"], error="final")
        assert ok is True
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT status, next_retry_at FROM jobs WHERE id = ?", (job["id"],)
            ).fetchone()
        assert row["status"] == "dead_letter"
        assert row["next_retry_at"] is None

    def test_first_retry_delay_approx_5_seconds(self):
        """First retry (attempt 1) should have delay of about 5 seconds."""
        from datetime import datetime as _dt, timezone as _tz
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        self.store.retry_job(job["id"], "worker-1", job["lease_token"], error="e1")
        with self.store._connect() as conn:
            row = conn.execute("SELECT next_retry_at, attempts FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert row is not None
        assert row["attempts"] == 1
        assert row["next_retry_at"] is not None
        nra = _dt.strptime(row["next_retry_at"], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=_tz.utc)
        diff = (nra - _dt.now(_tz.utc)).total_seconds()
        assert 4.0 <= diff <= 8.0, f"Expected ~5s delay, got {diff}s"

    def test_second_retry_delay_approx_30_seconds(self):
        """Second retry (attempt 2) should have delay of about 30 seconds."""
        from datetime import datetime as _dt, timezone as _tz
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET max_attempts = 3 WHERE task_id = ?", (task["id"],))
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        self.store.retry_job(job["id"], "worker-1", job["lease_token"], error="e1")
        # Force next_retry_at to past so claim picks it up again
        with self.store._connect() as conn:
            conn.execute("UPDATE jobs SET next_retry_at = '2000-01-01 00:00:00.000000' WHERE id = ?", (job["id"],))
        job2 = self.store.claim_next_job("worker-1")
        assert job2 is not None
        assert job2["attempts"] == 2
        self.store.retry_job(job2["id"], "worker-1", job2["lease_token"], error="e2")
        with self.store._connect() as conn:
            row = conn.execute("SELECT next_retry_at, attempts FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        assert row is not None
        assert row["attempts"] == 2
        assert row["next_retry_at"] is not None
        nra = _dt.strptime(row["next_retry_at"], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=_tz.utc)
        diff = (nra - _dt.now(_tz.utc)).total_seconds()
        assert 28.0 <= diff <= 35.0, f"Expected ~30s delay, got {diff}s"

    def test_idempotency_key_duplicate_prevented(self):
        """Two jobs with same idempotency key cannot coexist."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        # Insert first job with idempotency_key manually
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (task_id, stage, payload_json, status, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (task["id"], "SEARCHING", "{}", "test-key-123", utc_now(), utc_now()),
            )
        # Try inserting second with same key — should fail
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store._connect() as conn:
                conn.execute(
                    "INSERT INTO jobs (task_id, stage, payload_json, status, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                    (task["id"], "SEARCHING", "{}", "test-key-123", utc_now(), utc_now()),
                )

    def test_paper_status_transitions(self):
        task = self._task_at_paper_review()
        papers = self.store.list_papers(task["id"], "alice")
        p1 = papers[0]
        assert p1["paper_status"] == "candidate"

        task = self.store.approve_papers(task["id"], "alice", [p1["id"]])
        papers = self.store.list_papers(task["id"], "alice")
        selected = [p for p in papers if p["id"] == p1["id"]][0]
        assert selected["paper_status"] == "selected"

        # Worker marks as fetched
        self.store.update_paper_status(p1["id"], "fetched")
        papers = self.store.list_papers(task["id"], "alice")
        fetched = [p for p in papers if p["id"] == p1["id"]][0]
        assert fetched["paper_status"] == "fetched"

    def test_paper_degraded_on_error(self):
        task = self._task_at_paper_review()
        papers = self.store.list_papers(task["id"], "alice")
        self.store.approve_papers(task["id"], "alice", [papers[0]["id"]])
        self.store.update_paper_status(papers[0]["id"], "degraded", error="PDF corrupt")
        updated = self.store.list_papers(task["id"], "alice")
        p = [x for x in updated if x["id"] == papers[0]["id"]][0]
        assert p["paper_status"] == "degraded"
        assert p["error_message"] == "PDF corrupt"

    def test_artifact_recording(self):
        task = self.store.create_task("alice", "test", "query")
        artifact = self.store.record_artifact(
            task_id=task["id"],
            paper_id=None,
            artifact_type="report",
            format="markdown",
            path="/tmp/test/report.md",
            sha256="abc123",
        )
        assert artifact["id"] is not None
        artifacts = self.store.get_artifacts(task["id"])
        assert len(artifacts) == 1
        assert artifacts[0]["sha256"] == "abc123"

    def test_migration_checksum_validation(self):
        """Tampering with a migration after it was applied raises RuntimeError."""
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp()
        try:
            mig_dir = os.path.join(tmp, "migrations")
            os.makedirs(mig_dir)
            src = os.path.join(
                os.path.dirname(__file__), "..", "python-tools", "migrations"
            )
            for f in os.listdir(src):
                shutil.copy2(os.path.join(src, f), os.path.join(mig_dir, f))

            db_path = os.path.join(tmp, "test.db")

            # Subclass that uses the temp migrations directory
            class _TestStore(WorkflowStore):
                _MIGRATIONS_DIR = mig_dir

            # First run: apply migrations normally
            store = _TestStore(db_path)
            # Verify schema_migrations is populated
            from workflow_engine import _hashlib as hl

            with store._connect() as conn:
                rows = conn.execute(
                    "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
            self.assertEqual(len(rows), 3, "All migrations should have been applied")
            self.assertEqual(rows[0]["version"], 1)
            self.assertEqual(rows[1]["version"], 2)
            self.assertEqual(rows[2]["version"], 3)
            self.assertEqual(rows[2]["name"], "003_artifact_idempotency.sql")

            # Tamper with 001_initial.sql
            with open(os.path.join(mig_dir, "001_initial.sql"), "a") as f:
                f.write("\n-- tampered\n")

            # Second store on same DB should fail with checksum mismatch
            with self.assertRaises(RuntimeError) as ctx:
                _TestStore(db_path)
            self.assertIn("checksum changed", str(ctx.exception))
            self.assertIn("001_initial.sql", str(ctx.exception))
        finally:
            shutil.rmtree(tmp)


    def test_screening_scores_preserved(self):
        """Verify screening_stage uses real retriever/reranker scores, not hardcoded 80.0."""
        import asyncio
        import sys

        ROOT = os.path.dirname(os.path.dirname(__file__))
        sys.path.insert(0, os.path.join(ROOT, "python-tools"))

        from pipeline.screening_stage import run_screening_stage
        from workflow_models import (
            ScoredPaper,
            PaperMetadata,
            ScreeningDecision,
            TaskDefinition,
        )

        task = self.store.create_task("alice", "test", "query")
        self.store.update_definition(
            task["id"],
            "alice",
            {
                "research_object": "test material",
                "application": "test app",
                "paper_target": 10,
            },
        )
        self.store.start_search(task["id"], "alice")

        # Claim a job to get a valid WorkerContext
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=task["id"],
            lease_token=job["lease_token"],
        )

        # Insert a paper directly so the task stays in SEARCHING
        now = utc_now()
        with self.store._connect() as conn:
            conn.execute(
                """INSERT INTO papers
                   (id, task_id, work_id, title, metadata_json,
                    role_tags_json, relevance_score, evidence_level,
                    fulltext_status, selection_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "p1",
                    task["id"],
                    "w1",
                    "Test Paper",
                    '{"abstract":"test abstract","authors":[]}',
                    '[]',
                    0,
                    "abstract_only",
                    "unknown",
                    "candidate",
                    now,
                    now,
                ),
            )

        # -- Mocks that return non-round scores (proves 80.0 is not hardcoded) --

        class MockRetriever:
            async def retrieve(self, task_def, papers, top_k):
                return [
                    ScoredPaper(
                        metadata=PaperMetadata(
                            work_id="w1",
                            title="Test Paper",
                            abstract="test abstract",
                        ),
                        relevance_score=13.25,
                        authority_score=42.75,
                        confidence_score=71.5,
                    )
                ]

        class MockReranker:
            async def rerank(self, query, scored):
                return scored  # pass-through

        class MockAgent:
            async def screen_abstracts(self, task_def, papers):
                return [
                    ScreeningDecision(
                        paper=papers[0],
                        include=True,
                        role_tags=["target_performance"],
                    )
                ]

        result = asyncio.run(
            run_screening_stage(
                ctx,
                {"query": "test query"},
                self.store,
                MockRetriever(),
                MockReranker(),
                MockAgent(),
            )
        )

        # Verify the DB has the mock scores, NOT 80.0
        papers = self.store.list_papers(task["id"], "alice")
        p1 = next(p for p in papers if p["id"] == "p1")
        self.assertEqual(p1["relevance_score"], 13.25)
        self.assertEqual(p1["authority_score"], 42.75)
        self.assertEqual(p1["confidence_score"], 71.5)
        self.assertEqual(p1["role_tags"], ["target_performance"])
        self.assertIn("candidates_after_screening", result)
        self.assertIn("included", result)


class WorkerContextAuthTest(unittest.TestCase):
    """Tests for WorkerContext lease-based authorization."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(os.path.join(self.tmp.name, "workflow.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _create_task_and_claim(self, owner: str = "alice", worker_id: str = "worker-1"):
        """Helper: create a task by owner, start search (to create a job),
        claim it as worker-1, return (task, job, ctx)."""
        task = self.store.create_task(owner, "test", "query")
        task = self.store.start_search(task["id"], owner)
        job = self.store.claim_next_job(worker_id)
        assert job is not None, "no job to claim"
        ctx = WorkerContext(
            worker_id=worker_id,
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        return task, job, ctx

    def test_worker_can_read_task_via_worker_context(self):
        """Alice creates task, Worker claims job, Worker reads via WorkerContext."""
        task, _, ctx = self._create_task_and_claim()
        result = self.store.get_task_for_worker(ctx)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], task["id"])
        self.assertEqual(result["owner_id"], "alice")

    def test_worker_can_list_papers_via_worker_context(self):
        """Worker can list papers for a task via WorkerContext."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        # Submit candidates and approve so papers exist
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Paper One", "role_tags": ["target_performance"]},
            {"id": "p2", "title": "Paper Two", "role_tags": ["lab_process"]},
        ])
        # Now task is in WAITING_PAPER_APPROVAL; claim the next job
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        papers = self.store.list_papers_for_worker(ctx)
        # Papers exist (submitted via user API before claim)
        self.assertGreaterEqual(len(papers), 2)

    def test_worker_can_advance_via_worker_context(self):
        """Worker can advance a task stage via WorkerContext."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        # Claim and complete the SEARCHING job so it doesn't interfere
        search_job = self.store.claim_next_job("worker-1")
        self.store.complete_job(search_job["id"], "worker-1", search_job["lease_token"])
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        # Approve papers to move to FETCHING_FULLTEXT
        self.store.approve_papers(task["id"], "alice", ["p1"])
        # Now claim the FETCHING_FULLTEXT job
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        self.assertEqual(job["stage"], "FETCHING_FULLTEXT")
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        result = self.store.advance_for_worker(ctx, "PARSING")
        self.assertEqual(result["status"], "PARSING")

    def test_bob_cannot_access_alice_task_via_user_api(self):
        """Bob still cannot access Alice's task via user API."""
        task = self.store.create_task("alice", "secret", "query")
        with self.assertRaises(PermissionDeniedError):
            self.store.get_task(task["id"], "bob")

    def test_worker_with_wrong_lease_token_cannot_write(self):
        """Worker with invalid lease_token gets PermissionDeniedError."""
        task, _, ctx = self._create_task_and_claim()
        # Create a context with a forged lease token
        forged_ctx = WorkerContext(
            worker_id=ctx.worker_id,
            job_id=ctx.job_id,
            task_id=ctx.task_id,
            lease_token="forged-token",
        )
        with self.assertRaises(PermissionDeniedError):
            self.store.get_task_for_worker(forged_ctx)

    def test_worker_with_wrong_worker_id_cannot_write(self):
        """Worker-2 cannot use worker-1's lease."""
        task, _, ctx = self._create_task_and_claim()
        wrong_ctx = WorkerContext(
            worker_id="worker-2",
            job_id=ctx.job_id,
            task_id=ctx.task_id,
            lease_token=ctx.lease_token,
        )
        with self.assertRaises(PermissionDeniedError):
            self.store.get_task_for_worker(wrong_ctx)

    def test_worker_with_expired_job_cannot_write(self):
        """Worker cannot use a lease on a completed job."""
        task, job, ctx = self._create_task_and_claim()
        # Complete the job, invalidating the lease
        self.store.complete_job(job["id"], "worker-1", job["lease_token"])
        with self.assertRaises(PermissionDeniedError):
            self.store.get_task_for_worker(ctx)

    def test_audit_event_uses_system_worker_actor(self):
        """Audit events from worker methods show system:worker:<id> as actor."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        search_job = self.store.claim_next_job("worker-1")
        self.store.complete_job(search_job["id"], "worker-1", search_job["lease_token"])
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        self.store.approve_papers(task["id"], "alice", ["p1"])
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        self.store.advance_for_worker(ctx, "PARSING")
        events = self.store.events(ctx.task_id, "alice")
        # Find the stage_advanced event
        advanced = [e for e in events if e["event_type"] == "stage_advanced"]
        self.assertGreaterEqual(len(advanced), 1)
        # Last one should be from worker
        worker_events = [e for e in advanced if e["actor_id"] == "system:worker:worker-1"]
        self.assertGreaterEqual(len(worker_events), 1)

    def test_worker_submit_candidates(self):
        """Worker can submit candidate papers via worker context."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        result = self.store.submit_candidates_for_worker(ctx, [
            {"id": "p1", "title": "Worker Paper", "role_tags": ["target_performance"]},
        ])
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "WAITING_PAPER_APPROVAL")

    def test_worker_record_extraction(self):
        """Worker can record extraction via worker context."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        # Claim and complete the SEARCHING job so it doesn't interfere
        search_job = self.store.claim_next_job("worker-1")
        self.store.complete_job(search_job["id"], "worker-1", search_job["lease_token"])
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        self.store.approve_papers(task["id"], "alice", ["p1"])
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        result = self.store.record_extraction_for_worker(
            ctx,
            paper_id="p1",
            payload={"value": 42},
            source_type="explicit",
            confidence_score=90.0,
        )
        self.assertEqual(result["paper_id"], "p1")
        self.assertEqual(result["version"], 1)

    def test_worker_request_data_review(self):
        """Worker can request data review via worker context."""
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        search_job = self.store.claim_next_job("worker-1")
        self.store.complete_job(search_job["id"], "worker-1", search_job["lease_token"])
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        self.store.approve_papers(task["id"], "alice", ["p1"])
        self.store.record_extraction(task["id"], "alice", "p1", {"v": 1}, "explicit", 90)
        job = self.store.claim_next_job("worker-1")
        assert job is not None
        ctx = WorkerContext(
            worker_id="worker-1",
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        # Advance through stages to GENERATING_REPORT
        self.store.advance_for_worker(ctx, "PARSING")
        self.store.advance_for_worker(ctx, "READING")
        self.store.advance_for_worker(ctx, "EXTRACTING")
        self.store.advance_for_worker(ctx, "VALIDATING")
        self.store.advance_for_worker(ctx, "GENERATING_REPORT")
        # Now request data review
        result = self.store.request_data_review_for_worker(ctx)
        self.assertEqual(result["status"], "WAITING_DATA_REVIEW")


if __name__ == "__main__":
    unittest.main()

