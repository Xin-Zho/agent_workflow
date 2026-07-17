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
    WorkflowStore,
)


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

    def test_fifo_jobs(self):
        first = self.store.create_task("alice", "first", "one")
        second = self.store.create_task("bob", "second", "two")
        self.store.start_search(first["id"], "alice")
        self.store.start_search(second["id"], "bob")

        job1 = self.store.next_job()
        job2 = self.store.next_job()
        self.assertEqual(job1["task_id"], first["id"])
        self.assertEqual(job2["task_id"], second["id"])
        self.store.finish_job(job1["id"])
        self.store.finish_job(job2["id"], "simulated failure")

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
            self.assertEqual(len(rows), 2, "Both migrations should have been applied")
            self.assertEqual(rows[0]["version"], 1)
            self.assertEqual(rows[1]["version"], 2)

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


if __name__ == "__main__":
    unittest.main()

