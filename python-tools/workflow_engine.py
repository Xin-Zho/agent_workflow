"""Persistent state machine for material-science literature workflows.

The engine deliberately contains no LLM or database-provider code.  Pi Agent,
search connectors and PDF readers consume queued jobs and write their results
back through this service.  Keeping orchestration deterministic makes tasks
resumable and auditable even when a model process fails.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    previous_status TEXT,
                    definition_json TEXT NOT NULL,
                    coverage_json TEXT NOT NULL,
                    clarification_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    actor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    work_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    role_tags_json TEXT NOT NULL,
                    relevance_score REAL,
                    authority_score REAL,
                    confidence_score REAL,
                    evidence_level TEXT NOT NULL DEFAULT 'abstract_only',
                    fulltext_status TEXT NOT NULL DEFAULT 'unknown',
                    selection_status TEXT NOT NULL DEFAULT 'candidate',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_papers_task ON papers(task_id);
                CREATE INDEX IF NOT EXISTS idx_papers_work ON papers(work_id);

                CREATE TABLE IF NOT EXISTS extractions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_by TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(paper_id, version)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_fifo ON jobs(status, id);

                CREATE TABLE IF NOT EXISTS lab_capabilities (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

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
                item[key[:-5]] = json.loads(item.pop(key))
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
                "UPDATE papers SET selection_status = 'selected', updated_at = ? WHERE id = ?",
                [(utc_now(), paper_id) for paper_id in selected_ids],
            )
        return self._transition(
            task, actor_id, TaskStatus.FETCHING_FULLTEXT,
            "papers_approved", {"selected_ids": selected_ids}, enqueue=True,
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

    def next_job(self) -> dict[str, Any] | None:
        """Claim the oldest queued job. SQLite transaction preserves FIFO order."""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                "UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
            conn.commit()
        job = self._decode_row(row)
        job["status"] = "running"
        job["attempts"] += 1
        return job

    def finish_job(self, job_id: int, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                ("failed" if error else "completed", error, utc_now(), job_id),
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
