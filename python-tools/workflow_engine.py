"""Persistent state machine for material-science literature workflows.

The engine deliberately contains no LLM or database-provider code.  Pi Agent,
search connectors and PDF readers consume queued jobs and write their results
back through this service.  Keeping orchestration deterministic makes tasks
resumable and auditable even when a model process fails.
"""

from __future__ import annotations

import hashlib as _hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(sep=' ')


ClaimedJob = dict[str, Any]  # returned by claim_next_job


class WorkflowError(RuntimeError):
    pass


class NotFoundError(WorkflowError):
    pass


class PermissionDeniedError(WorkflowError):
    pass


class InvalidTransitionError(WorkflowError):
    pass


class TaskStatus(StrEnum):
    DRAFT = "DRAFT"
    CLARIFYING = "CLARIFYING"
    SEARCHING = "SEARCHING"
    SCREENING = "SCREENING"
    WAITING_PAPER_APPROVAL = "WAITING_PAPER_APPROVAL"
    FETCHING_FULLTEXT = "FETCHING_FULLTEXT"
    PARSING = "PARSING"
    READING = "READING"
    EXTRACTING = "EXTRACTING"
    VALIDATING = "VALIDATING"
    GENERATING_REPORT = "GENERATING_REPORT"
    WAITING_DATA_REVIEW = "WAITING_DATA_REVIEW"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


ACTIVE_PIPELINE = [
    TaskStatus.SEARCHING,
    TaskStatus.SCREENING,
    TaskStatus.WAITING_PAPER_APPROVAL,
    TaskStatus.FETCHING_FULLTEXT,
    TaskStatus.PARSING,
    TaskStatus.READING,
    TaskStatus.EXTRACTING,
    TaskStatus.VALIDATING,
    TaskStatus.GENERATING_REPORT,
    TaskStatus.WAITING_DATA_REVIEW,
    TaskStatus.COMPLETED,
]

DEFAULT_CLARIFICATION_QUESTIONS = [
    "研究对象和应用场景是什么？",
    "目标性能指标、单位和期望范围是什么？",
    "哪些是硬约束，哪些是优化目标或可牺牲指标？",
    "当前任务对材料、安全、设备和制备周期有哪些额外限制？",
    "期望候选论文数量是多少？",
]

DEFAULT_COVERAGE = {
    "target_performance": 1,
    "structure": 1,
    "lab_process": 1,
    "composition_ratio": 1,
    "authoritative_validation": 1,
}


class WorkflowStore:
    """SQLite-backed workflow repository and transition service."""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    # Class-level override for tests to use a custom migrations directory
    _MIGRATIONS_DIR: str | None = None

    def _initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Apply pending SQL migrations with checksum validation."""
        migrations_dir = (
            Path(self._MIGRATIONS_DIR)
            if self._MIGRATIONS_DIR
            else Path(__file__).resolve().parent / "migrations"
        )
        if not migrations_dir.is_dir():
            return

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Ensure schema_migrations table exists (001 itself may not have been applied)
                conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )""")

                # Load already-applied versions
                applied = {
                    row["version"]: row["checksum"]
                    for row in conn.execute(
                        "SELECT version, checksum FROM schema_migrations ORDER BY version"
                    ).fetchall()
                }

                # Find SQL files
                sql_files = sorted(
                    migrations_dir.glob("*.sql"),
                    key=lambda p: int(p.stem.split("_")[0]) if p.stem.split("_")[0].isdigit() else 0,
                )

                for sql_file in sql_files:
                    version = int(sql_file.stem.split("_")[0])
                    sql_text = sql_file.read_text(encoding="utf-8")
                    current_checksum = _hashlib.sha256(sql_text.encode()).hexdigest()

                    if version in applied:
                        if applied[version] != current_checksum:
                            raise RuntimeError(
                                f"Migration {version} ({sql_file.name}) checksum changed. "
                                f"Stored: {applied[version][:16]}..., Current: {current_checksum[:16]}..."
                            )
                        continue  # already applied with matching checksum

                    conn.executescript(sql_text)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                        (version, sql_file.name, current_checksum, utc_now()),
                    )

                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for key in list(item):
            if key.endswith("_json"):
                val = item.pop(key)
                item[key[:-5]] = json.loads(val) if val is not None else None
        return item

    def _event(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        actor_id: str,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO task_events
               (task_id, actor_id, event_type, from_status, to_status,
                details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, actor_id, event_type, from_status, to_status,
             self._json(details or {}), utc_now()),
        )

    def create_task(
        self,
        owner_id: str,
        title: str,
        query: str,
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        now = utc_now()
        payload = definition or {}
        payload.setdefault("languages", ["zh", "en"])
        payload.setdefault("year_policy", "recent_5_years_plus_foundational")
        payload.setdefault("temporary_lab_constraints", [])
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO tasks
                   (id, owner_id, title, query, status, previous_status,
                    definition_json, coverage_json, clarification_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)""",
                (task_id, owner_id, title, query, TaskStatus.CLARIFYING,
                 self._json(payload), self._json(DEFAULT_COVERAGE),
                 self._json(DEFAULT_CLARIFICATION_QUESTIONS), now, now),
            )
            self._event(conn, task_id, owner_id, "task_created", None,
                        TaskStatus.CLARIFYING, {"query": query})
        return self.get_task(task_id, owner_id)

    def get_task(self, task_id: str, actor_id: str, is_admin: bool = False) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        task = self._decode_row(row)
        if not task:
            raise NotFoundError(f"Task not found: {task_id}")
        if task["owner_id"] != actor_id and not is_admin:
            raise PermissionDeniedError("Task belongs to another user")
        return task

    def list_tasks(self, actor_id: str, is_admin: bool = False) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if is_admin:
                rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE owner_id = ? ORDER BY created_at DESC",
                    (actor_id,),
                ).fetchall()
        return [self._decode_row(row) for row in rows]

    def update_definition(
        self,
        task_id: str,
        actor_id: str,
        definition: dict[str, Any],
        coverage: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] not in {TaskStatus.CLARIFYING, TaskStatus.DRAFT}:
            raise InvalidTransitionError("Definition can only be edited before search")
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET definition_json = ?, coverage_json = ?, updated_at = ? WHERE id = ?",
                (self._json(definition), self._json(coverage or task["coverage"]), utc_now(), task_id),
            )
            self._event(conn, task_id, actor_id, "definition_updated",
                        task["status"], task["status"])
        return self.get_task(task_id, actor_id)

    def start_search(self, task_id: str, actor_id: str) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] not in {TaskStatus.CLARIFYING, TaskStatus.DRAFT}:
            raise InvalidTransitionError("Only a clarified task can start searching")
        return self._transition(task, actor_id, TaskStatus.SEARCHING,
                                "search_started", enqueue=True)

    def submit_candidates(
        self,
        task_id: str,
        actor_id: str,
        papers: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] not in {TaskStatus.SEARCHING, TaskStatus.SCREENING}:
            raise InvalidTransitionError("Candidates are only accepted during search/screening")
        now = utc_now()
        count = 0
        with self._connect() as conn:
            for paper in papers:
                paper_id = paper.get("id") or uuid.uuid4().hex
                work_id = paper.get("work_id") or self._work_id(paper)
                metadata = dict(paper.get("metadata") or {})
                for key in ("authors", "year", "abstract", "doi", "source",
                            "document_type", "url", "language"):
                    if key in paper:
                        metadata[key] = paper[key]
                conn.execute(
                    """INSERT OR REPLACE INTO papers
                       (id, task_id, work_id, title, metadata_json, role_tags_json,
                        relevance_score, authority_score, confidence_score,
                        evidence_level, fulltext_status, selection_status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (paper_id, task_id, work_id, paper["title"], self._json(metadata),
                     self._json(paper.get("role_tags", [])), paper.get("relevance_score"),
                     paper.get("authority_score"), paper.get("confidence_score"),
                     paper.get("evidence_level", "abstract_only"),
                     paper.get("fulltext_status", "unknown"), "candidate", now, now),
                )
                count += 1
            conn.execute(
                "UPDATE tasks SET status = ?, previous_status = ?, updated_at = ? WHERE id = ?",
                (TaskStatus.WAITING_PAPER_APPROVAL, task["status"], now, task_id),
            )
            self._event(conn, task_id, actor_id, "candidates_submitted",
                        task["status"], TaskStatus.WAITING_PAPER_APPROVAL,
                        {"count": count})
        return self.get_task(task_id, actor_id)

    @staticmethod
    def _work_id(paper: dict[str, Any]) -> str:
        doi = str(paper.get("doi") or "").strip().lower()
        if doi:
            return "doi:" + doi.removeprefix("https://doi.org/")
        normalized = " ".join(str(paper.get("title", "")).lower().split())
        return "title:" + hashlib_sha256(normalized)[:20]

    def list_papers(self, task_id: str, actor_id: str, is_admin: bool = False) -> list[dict[str, Any]]:
        self.get_task(task_id, actor_id, is_admin)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM papers WHERE task_id = ? ORDER BY relevance_score DESC, created_at",
                (task_id,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def approve_papers(
        self,
        task_id: str,
        actor_id: str,
        selected_ids: list[str],
    ) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] != TaskStatus.WAITING_PAPER_APPROVAL:
            raise InvalidTransitionError("Task is not waiting for paper approval")
        with self._connect() as conn:
            known = {r["id"] for r in conn.execute(
                "SELECT id FROM papers WHERE task_id = ?", (task_id,)
            ).fetchall()}
            unknown = set(selected_ids) - known
            if unknown:
                raise NotFoundError(f"Unknown paper ids: {sorted(unknown)}")
            conn.execute("UPDATE papers SET selection_status = 'rejected' WHERE task_id = ?", (task_id,))
            conn.executemany(
                "UPDATE papers SET selection_status = 'selected', paper_status = 'selected', updated_at = ? WHERE id = ?",
                [(utc_now(), paper_id) for paper_id in selected_ids],
            )
        return self._transition(
            task, actor_id, TaskStatus.FETCHING_FULLTEXT,
            "papers_approved", {"selected_ids": selected_ids}, enqueue=True,
        )

    def update_paper_status(
        self, paper_id: str, status: str, error: str | None = None
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE papers SET paper_status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, error, now, paper_id),
            )

    def record_extraction(
        self,
        task_id: str,
        actor_id: str,
        paper_id: str,
        payload: dict[str, Any],
        source_type: str,
        confidence_score: float,
    ) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] not in {
            TaskStatus.FETCHING_FULLTEXT, TaskStatus.PARSING, TaskStatus.READING,
            TaskStatus.EXTRACTING, TaskStatus.VALIDATING, TaskStatus.GENERATING_REPORT,
        }:
            raise InvalidTransitionError("Task is not in the full-text processing pipeline")
        with self._connect() as conn:
            paper = conn.execute(
                "SELECT id FROM papers WHERE id = ? AND task_id = ?", (paper_id, task_id)
            ).fetchone()
            if not paper:
                raise NotFoundError(f"Paper not found: {paper_id}")
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM extractions WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()[0]
            extraction_id = uuid.uuid4().hex
            conn.execute(
                "UPDATE extractions SET review_status = 'superseded' WHERE paper_id = ? AND review_status = 'pending'",
                (paper_id,),
            )
            conn.execute(
                """INSERT INTO extractions
                   (id, task_id, paper_id, version, payload_json, source_type,
                    confidence_score, review_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (extraction_id, task_id, paper_id, version, self._json(payload),
                 source_type, confidence_score, utc_now()),
            )
            self._event(conn, task_id, actor_id, "extraction_recorded",
                        task["status"], task["status"],
                        {"paper_id": paper_id, "version": version})
        return {"id": extraction_id, "paper_id": paper_id, "version": version}

    def request_data_review(self, task_id: str, actor_id: str) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] not in {
            TaskStatus.EXTRACTING, TaskStatus.VALIDATING, TaskStatus.GENERATING_REPORT,
        }:
            raise InvalidTransitionError("Task has not finished extraction")
        return self._transition(task, actor_id, TaskStatus.WAITING_DATA_REVIEW,
                                "data_review_requested")

    def review_extractions(
        self,
        task_id: str,
        actor_id: str,
        approved: bool,
        is_admin: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id, is_admin)
        if task["status"] != TaskStatus.WAITING_DATA_REVIEW:
            raise InvalidTransitionError("Task is not waiting for data review")
        status = "approved" if approved else "rejected"
        target = TaskStatus.COMPLETED if approved else TaskStatus.EXTRACTING
        with self._connect() as conn:
            conn.execute(
                "UPDATE extractions SET review_status = ?, reviewed_by = ? WHERE task_id = ? AND review_status = 'pending'",
                (status, actor_id, task_id),
            )
        return self._transition(task, actor_id, target, "data_reviewed",
                                {"approved": approved, "notes": notes})

    def list_extractions(self, task_id: str, actor_id: str, is_admin: bool = False) -> list[dict[str, Any]]:
        self.get_task(task_id, actor_id, is_admin)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.* FROM extractions e
                   JOIN (SELECT paper_id, MAX(version) AS version FROM extractions
                         WHERE task_id = ? GROUP BY paper_id) latest
                   ON e.paper_id = latest.paper_id AND e.version = latest.version
                   ORDER BY e.created_at""",
                (task_id,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def record_artifact(
        self,
        task_id: str,
        paper_id: str | None,
        artifact_type: str,
        format: str,
        path: str,
        sha256: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        artifact_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO artifacts (id, task_id, paper_id, artifact_type, format, path, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact_id, task_id, paper_id, artifact_type, format, path, sha256, now),
            )
        return {"id": artifact_id, "path": path, "sha256": sha256}

    def get_artifacts(
        self, task_id: str, artifact_type: str | None = None
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if artifact_type:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE task_id = ? AND artifact_type = ? ORDER BY created_at",
                    (task_id, artifact_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task_id,),
                ).fetchall()
        return [self._decode_row(row) for row in rows]

    def record_report(self, task_id: str, path: str, format: str = "markdown") -> dict:
        now = utc_now()
        report_id = uuid.uuid4().hex
        with self._connect() as conn:
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM reports WHERE task_id = ?", (task_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO reports (id, task_id, version, format, path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (report_id, task_id, version, format, path, now),
            )
        return {"id": report_id, "version": version, "path": path}

    def get_reports(self, task_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reports WHERE task_id = ? ORDER BY version DESC", (task_id,),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def advance(self, task_id: str, actor_id: str, target: str) -> dict[str, Any]:
        """Worker-facing deterministic advance between automated stages."""
        task = self.get_task(task_id, actor_id)
        target_status = TaskStatus(target)
        allowed = {
            TaskStatus.FETCHING_FULLTEXT: TaskStatus.PARSING,
            TaskStatus.PARSING: TaskStatus.READING,
            TaskStatus.READING: TaskStatus.EXTRACTING,
            TaskStatus.EXTRACTING: TaskStatus.VALIDATING,
            TaskStatus.VALIDATING: TaskStatus.GENERATING_REPORT,
        }
        if allowed.get(TaskStatus(task["status"])) != target_status:
            raise InvalidTransitionError(f"Cannot advance {task['status']} to {target_status}")
        return self._transition(task, actor_id, target_status, "stage_advanced", enqueue=True)

    def pause(self, task_id: str, actor_id: str) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] in {TaskStatus.COMPLETED, TaskStatus.PAUSED}:
            raise InvalidTransitionError("Task cannot be paused")
        # Running workers must check the task status before committing results;
        # queued work is cancelled here and recreated when the task resumes.
        self._cancel_queued_jobs(task_id)
        return self._transition(task, actor_id, TaskStatus.PAUSED, "task_paused")

    def resume(self, task_id: str, actor_id: str) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        if task["status"] != TaskStatus.PAUSED or not task["previous_status"]:
            raise InvalidTransitionError("Task is not paused")
        return self._transition(task, actor_id, TaskStatus(task["previous_status"]),
                                "task_resumed", enqueue=True)

    def rollback(self, task_id: str, actor_id: str, target: str) -> dict[str, Any]:
        task = self.get_task(task_id, actor_id)
        target_status = TaskStatus(target)
        allowed_targets = set(ACTIVE_PIPELINE[:-1]) | {TaskStatus.CLARIFYING}
        if target_status not in allowed_targets:
            raise InvalidTransitionError("Invalid rollback target")
        self._cancel_queued_jobs(task_id)
        return self._transition(task, actor_id, target_status, "task_rolled_back",
                                enqueue=target_status not in {
                                    TaskStatus.CLARIFYING, TaskStatus.WAITING_PAPER_APPROVAL,
                                    TaskStatus.WAITING_DATA_REVIEW,
                                })

    def _cancel_queued_jobs(self, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE task_id = ? AND status = 'queued'",
                (utc_now(), task_id),
            )

    def _transition(
        self,
        task: dict[str, Any],
        actor_id: str,
        target: TaskStatus,
        event_type: str,
        details: dict[str, Any] | None = None,
        enqueue: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        previous = task["status"]
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, previous_status = ?, updated_at = ? WHERE id = ?",
                (target, previous, now, task["id"]),
            )
            self._event(conn, task["id"], actor_id, event_type, previous, target, details)
            if enqueue:
                conn.execute(
                    """INSERT INTO jobs
                       (task_id, stage, payload_json, status, attempts, created_at, updated_at)
                       VALUES (?, ?, ?, 'queued', 0, ?, ?)""",
                    (task["id"], target, self._json(details or {}), now, now),
                )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
        # Authorization was checked before entering the transition.  Reading the
        # updated row directly also lets an administrator review another user's task.
        return self._decode_row(row)

    def claim_next_job(self, worker_id: str, lease_duration: int = 300) -> ClaimedJob | None:
        """Claim the oldest eligible job with lease fencing."""
        import uuid as _uuid
        from datetime import timedelta as _timedelta
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(sep=' ')
        lease_token = _uuid.uuid4().hex
        lease_expires_at = (now_dt + _timedelta(seconds=lease_duration)).isoformat(sep=' ')
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # First, promote dead letters
                conn.execute(
                    """UPDATE jobs SET status = 'dead_letter', updated_at = ?
                       WHERE ((status = 'retry_wait' AND next_retry_at <= ? AND attempts >= max_attempts)
                          OR (status = 'running' AND lease_expires_at < ? AND attempts >= max_attempts))""",
                    (now, now, now),
                )
                row = conn.execute(
                    """UPDATE jobs SET
                           status = 'running',
                           worker_id = ?,
                           claimed_at = ?,
                           lease_expires_at = ?,
                           lease_token = ?,
                           attempts = attempts + 1
                       WHERE id = (
                           SELECT id FROM jobs
                           WHERE (status = 'queued')
                              OR (status = 'running' AND lease_expires_at < ?)
                              OR (status = 'retry_wait' AND next_retry_at <= ?)
                           ORDER BY id LIMIT 1
                       )
                       AND attempts < max_attempts
                       RETURNING *""",
                    (worker_id, now, lease_expires_at, lease_token, now, now),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if row is None:
            return None
        return dict(row)

    def complete_job(self, job_id: int, worker_id: str, lease_token: str,
                     result: dict[str, Any] | None = None) -> bool:
        """Complete a job — fenced by worker_id + lease_token."""
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE jobs SET status = 'completed', result_json = ?, updated_at = ?
                   WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'""",
                (self._json(result or {}), now, job_id, worker_id, lease_token),
            )
            if cur.rowcount == 0:
                return False
            # Record attempt
            conn.execute(
                """INSERT INTO job_attempts (job_id, worker_id, lease_token, attempt_num,
                   started_at, finished_at, result_json)
                   SELECT ?, ?, ?, attempts, claimed_at, ?, ? FROM jobs WHERE id = ?""",
                (job_id, worker_id, lease_token, now, self._json(result or {}), job_id),
            )
            return True

    def retry_job(self, job_id: int, worker_id: str, lease_token: str,
                  error: str = "") -> bool:
        """Mark job for retry after backoff — fenced."""
        now = utc_now()
        with self._connect() as conn:
            # Record failed attempt
            conn.execute(
                """INSERT INTO job_attempts (job_id, worker_id, lease_token, attempt_num,
                   started_at, finished_at, error)
                   SELECT ?, ?, ?, attempts, claimed_at, ?, ? FROM jobs WHERE id = ?""",
                (job_id, worker_id, lease_token, now, error, job_id),
            )
            # Determine delay based on attempts
            row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return False
            delay = {1: 5, 2: 30}.get(row["attempts"], 30)
            # If max attempts reached, set next_retry_at to now so
            # _promote_dead_letters or the inline promotion in claim_next_job
            # immediately moves it to dead_letter.
            retry_delay = 0 if row["attempts"] >= row["max_attempts"] else delay
            cur = conn.execute(
                """UPDATE jobs SET status = 'retry_wait', next_retry_at = datetime(?, '+' || ? || ' seconds'),
                   worker_id = NULL, lease_token = NULL, error = ?, updated_at = ?
                   WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'""",
                (now, str(retry_delay), error, now, job_id, worker_id, lease_token),
            )
            return cur.rowcount > 0

    def fail_job(self, job_id: int, worker_id: str, lease_token: str,
                 error: str = "") -> bool:
        """Permanently fail a job — fenced."""
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO job_attempts (job_id, worker_id, lease_token, attempt_num,
                   started_at, finished_at, error)
                   SELECT ?, ?, ?, attempts, claimed_at, ?, ? FROM jobs WHERE id = ?""",
                (job_id, worker_id, lease_token, now, error, job_id),
            )
            cur = conn.execute(
                """UPDATE jobs SET status = 'failed', error = ?, updated_at = ?
                   WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'""",
                (error, now, job_id, worker_id, lease_token),
            )
            return cur.rowcount > 0

    def renew_lease(self, job_id: int, worker_id: str, lease_token: str,
                    lease_duration: int = 300) -> bool:
        """Extend lease — fenced. Returns False if lease was lost."""
        from datetime import timedelta as _timedelta
        now_dt = datetime.now(timezone.utc)
        lease_expires_at = (now_dt + _timedelta(seconds=lease_duration)).isoformat(sep=' ')
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE jobs SET lease_expires_at = ?
                   WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'""",
                (lease_expires_at, job_id, worker_id, lease_token),
            )
            return cur.rowcount > 0

    def _promote_dead_letters(self) -> None:
        """Move maxed-out jobs to dead_letter."""
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status = 'dead_letter', updated_at = ?
                   WHERE ((status = 'retry_wait' AND next_retry_at <= ? AND attempts >= max_attempts)
                      OR (status = 'running' AND lease_expires_at < ? AND attempts >= max_attempts))""",
                (now, now, now),
            )

    def events(self, task_id: str, actor_id: str, is_admin: bool = False) -> list[dict[str, Any]]:
        self.get_task(task_id, actor_id, is_admin)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [self._decode_row(row) for row in rows]


def hashlib_sha256(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
