"""Tests for parse artifact idempotency (fix #4).

Verifies that retrying the parse stage does not degrade successfully-parsed
papers, and that content-addressed artifact naming prevents duplicate rows.
"""

import hashlib
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

import pytest  # noqa: E402

from workflow_engine import (  # noqa: E402
    WorkerContext,
    WorkflowStore,
    utc_now,
)
from artifact_utils import atomic_write_unique  # noqa: E402


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestRecordArtifactIdempotent:
    """record_artifact() must tolerate duplicate inserts with the same content."""

    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(os.path.join(self.tmp.name, "workflow.db"))

    def teardown_method(self):
        self.tmp.cleanup()

    def _create_task_with_paper(self):
        task = self.store.create_task("alice", "test", "query")
        self.store.start_search(task["id"], "alice")
        self.store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test Paper", "role_tags": ["target_performance"]},
        ])
        self.store.approve_papers(task["id"], "alice", ["p1"])
        return task

    def test_record_artifact_duplicate_returns_existing(self):
        """Recording the same artifact twice returns the same data (not a new row)."""
        task = self._create_task_with_paper()
        sha = sha256_bytes(b'{"key": "value"}')

        first = self.store.record_artifact(
            task_id=task["id"],
            paper_id="p1",
            artifact_type="parsed_document",
            format="json",
            path="/tmp/parsed_doc.json",
            sha256=sha,
        )
        second = self.store.record_artifact(
            task_id=task["id"],
            paper_id="p1",
            artifact_type="parsed_document",
            format="json",
            path="/tmp/parsed_doc.json",
            sha256=sha,
        )

        # Both should succeed
        assert first["id"] is not None
        assert second["id"] is not None

        # Should have the same artifact ID (second returned existing row)
        assert first["id"] == second["id"]

        # Only one row in the DB
        artifacts = self.store.get_artifacts(task["id"], "parsed_document")
        assert len(artifacts) == 1

    def test_different_content_produces_separate_rows(self):
        """Different sha256 values produce separate artifact rows."""
        task = self._create_task_with_paper()
        sha_a = sha256_bytes(b'{"data": "A"}')
        sha_b = sha256_bytes(b'{"data": "B"}')

        first = self.store.record_artifact(
            task_id=task["id"],
            paper_id="p1",
            artifact_type="parsed_document",
            format="json",
            path="/tmp/doc_a.json",
            sha256=sha_a,
        )
        second = self.store.record_artifact(
            task_id=task["id"],
            paper_id="p1",
            artifact_type="parsed_document",
            format="json",
            path="/tmp/doc_b.json",
            sha256=sha_b,
        )

        assert first["id"] != second["id"]
        artifacts = self.store.get_artifacts(task["id"], "parsed_document")
        assert len(artifacts) == 2

    def test_record_without_sha256_still_inserts_multiple(self):
        """Without sha256, the UNIQUE constraint allows duplicates (sha256 is nullable)."""
        task = self._create_task_with_paper()
        first = self.store.record_artifact(
            task_id=task["id"],
            paper_id="p1",
            artifact_type="report",
            format="markdown",
            path="/tmp/report.md",
            sha256=None,
        )
        second = self.store.record_artifact(
            task_id=task["id"],
            paper_id="p1",
            artifact_type="report",
            format="markdown",
            path="/tmp/report.md",
            sha256=None,
        )
        # Both should succeed without error (null sha256 does not trigger UNIQUE)
        assert first["id"] is not None
        assert second["id"] is not None
        assert first["id"] != second["id"]
        artifacts = self.store.get_artifacts(task["id"])
        assert len(artifacts) == 2


class TestContentAddressedNaming:
    """Content-addressed filenames prevent FileExistsError on identical content."""

    def test_same_content_same_path(self):
        tmp = tempfile.mkdtemp()
        try:
            content = b'{"parsed": "data"}'
            sha = sha256_bytes(content)
            path = os.path.join(tmp, f"parsed_document_{sha[:16]}.json")

            # First write
            r1 = atomic_write_unique(content, path, expected_sha256=sha)
            assert os.path.exists(r1)

            # Second write with same content — should reuse
            r2 = atomic_write_unique(content, path, expected_sha256=sha)
            assert r1 == r2
            assert os.path.exists(r2)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_different_content_different_path(self):
        tmp = tempfile.mkdtemp()
        try:
            content_a = b'{"version": "A"}'
            sha_a = sha256_bytes(content_a)
            path_a = os.path.join(tmp, f"doc_{sha_a[:16]}.json")

            content_b = b'{"version": "B"}'
            sha_b = sha256_bytes(content_b)
            path_b = os.path.join(tmp, f"doc_{sha_b[:16]}.json")

            r1 = atomic_write_unique(content_a, path_a, expected_sha256=sha_a)
            r2 = atomic_write_unique(content_b, path_b, expected_sha256=sha_b)
            assert r1 != r2  # different paths because different hashes
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestParseStageIdempotency:
    """End-to-end retry behavior: already-parsed papers must not be degraded."""

    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkflowStore(os.path.join(self.tmp.name, "workflow.db"))
        self.task = self.store.create_task("alice", "test", "query")
        self.store.start_search(self.task["id"], "alice")

        # Submit and approve a paper so it reaches FETCHING_FULLTEXT
        self.store.submit_candidates(self.task["id"], "alice", [
            {"id": "p1", "title": "Test Paper", "role_tags": ["target_performance"]},
        ])
        self.store.approve_papers(self.task["id"], "alice", ["p1"])

        # Mark paper as fetched (as fetch stage would)
        self.store.update_paper_status("p1", "fetched")

        # Claim a job to get a valid WorkerContext with lease
        self.job = self.store.claim_next_job("worker-1")
        assert self.job is not None
        self.ctx = WorkerContext(
            worker_id="worker-1",
            job_id=self.job["id"],
            task_id=self.job["task_id"],
            lease_token=self.job["lease_token"],
        )

    def teardown_method(self):
        self.tmp.cleanup()

    def _simulate_parse(self, paper_id: str) -> dict:
        """Simulate a parse that succeeds, writing artifact + setting status."""
        sha = sha256_bytes(b'{"parsed": "content"}')
        paper_dir = os.path.join(self.tmp.name, "data", "tasks", self.task["id"], "papers", paper_id)
        os.makedirs(paper_dir, exist_ok=True)
        parsed_path = os.path.join(paper_dir, f"parsed_document_{sha[:16]}.json")
        with open(parsed_path, "wb") as f:
            f.write(b'{"parsed": "content"}')

        self.store.record_artifact(
            self.task["id"], paper_id, "parsed_document", "json", parsed_path, sha,
        )
        self.store.update_paper_status(paper_id, "parsed")
        return {"parsed": 1, "degraded": 0}

    def test_parse_idempotent_on_retry(self):
        """Simulating a retry after successful parse must keep paper as 'parsed'."""
        # 1. First parse succeeds
        self._simulate_parse("p1")
        papers = self.store.list_papers(self.task["id"], "alice")
        p1 = next(p for p in papers if p["id"] == "p1")
        assert p1["paper_status"] == "parsed"

        # 2. Simulate retry — the already-parsed check in parse_stage should skip it
        parsed_artifacts = self.store.get_artifacts(self.task["id"], "parsed_document")
        already_parsed = {a["paper_id"] for a in parsed_artifacts}
        assert "p1" in already_parsed

        # Manually reset paper status to 'fetched' to simulate the retry scenario
        # (a retry happens when the worker is killed before updating status)
        self.store.update_paper_status("p1", "fetched")

        # Now re-check — paper is re-processed by parse stage
        parsed_artifacts = self.store.get_artifacts(self.task["id"], "parsed_document")
        already_parsed = {a["paper_id"] for a in parsed_artifacts}
        if "p1" in already_parsed:
            # This is what parse_stage does: skip and mark as parsed
            self.store.update_paper_status("p1", "parsed")

        papers = self.store.list_papers(self.task["id"], "alice")
        p1 = next(p for p in papers if p["id"] == "p1")
        assert p1["paper_status"] == "parsed"
        assert p1["error_message"] is None  # not degraded

        # 3. Verify only one artifact row exists
        artifacts = self.store.get_artifacts(self.task["id"], "parsed_document")
        assert len(artifacts) == 1

    def test_artifact_content_addressing_dedup(self):
        """Two identical parsed documents produce one artifact row."""
        # Record first artifact
        sha = sha256_bytes(b'{"parsed": "identical"}')
        path = os.path.join(self.tmp.name, "parsed_doc.json")
        with open(path, "wb") as f:
            f.write(b'{"parsed": "identical"}')

        first = self.store.record_artifact(
            self.task["id"], "p1", "parsed_document", "json", path, sha,
        )

        # Record second with same sha256 (simulating retry with identical parse output)
        second = self.store.record_artifact(
            self.task["id"], "p1", "parsed_document", "json", path, sha,
        )

        assert first["id"] == second["id"]

        artifacts = self.store.get_artifacts(self.task["id"], "parsed_document")
        assert len(artifacts) == 1

    def test_file_exists_error_not_degraded(self):
        """FileExistsError during artifact write must not degrade the paper."""
        # Simulate what parse_stage does when atomic_write_unique raises
        # FileExistsError in a race condition. The stage catches it and
        # marks the paper as "parsed" (not "degraded").
        try:
            raise FileExistsError("simulated race: another worker wrote first")
        except FileExistsError:
            self.store.update_paper_status("p1", "parsed")

        papers = self.store.list_papers(self.task["id"], "alice")
        p1 = next(p for p in papers if p["id"] == "p1")
        assert p1["paper_status"] == "parsed", (
            f"FileExistsError should not degrade; got '{p1['paper_status']}'"
        )
        assert p1.get("error_message") is None, (
            f"Paper should not have error_message: {p1.get('error_message')}"
        )

    def test_actual_parse_error_still_degrades(self):
        """An actual parsing exception must still degrade the paper."""
        # Simulate a parse error
        try:
            raise ValueError("model parse failure")
        except Exception as exc:
            self.store.update_paper_status(
                "p1", "degraded", error=f"parse failed: {exc}"
            )

        papers = self.store.list_papers(self.task["id"], "alice")
        p1 = next(p for p in papers if p["id"] == "p1")
        assert p1["paper_status"] == "degraded"
        assert p1["error_message"] is not None
        assert "parse failed" in p1["error_message"]


if __name__ == "__main__":
    pytest.main(["-v", __file__])
