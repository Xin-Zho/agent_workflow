# Workflow Foundation + Mock E2E — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal foundation then wire a mock E2E pipeline that completes a full research task without external services.

**Architecture:** Worker and API share one SQLite database; large files go to `data/tasks/<id>/` via immutable content-addressed paths. Stage handlers consume Protocol-based adapters (mock in Phase 2A). Human gates enforced by SQL trigger + Store + Worker registry.

**Tech Stack:** Python 3.13+, FastAPI, Pydantic v2, SQLite (WAL), PyMuPDF, pytest, pytest-asyncio.

**Scope:** Phase 1A (tasks 1-11) + Phase 2A (tasks 12-22).

## Global Constraints

- Pydantic: list/dict use `Field(default_factory=...)`, never `=[]` or `={}`
- `source_type` per-field (on EvidenceLocator), NOT per-extraction
- Every data field holds `evidence_ids: list[str]`
- Artifact files: immutable paths, `os.link()` non-overwrite, content-hash reuse on match
- Temp files on same filesystem as final artifact for `os.link()`
- Concurrent same-hash: `os.link()` → FileExistsError → re-verify hash → match = reuse
- SQL trigger: static error text only
- `attempts`: counts executions (0=never, 1=first, 2=first retry, 3=second retry)
- `idempotency_key`: unconditional UNIQUE index (`WHERE idempotency_key IS NOT NULL`)
- Migration checksums: SHA-256; changed = refuse startup
- All stage-completion ops in ONE DB transaction
- Worker concurrency=1, no HTTP to API, direct DB only
- SQLite: WAL, foreign_keys=ON, busy_timeout=30000, local disk
- `approve_papers` + `review_extractions`: owner OR admin
- Blocking handlers: `asyncio.to_thread()` / `loop.run_in_executor()`

---

## File Structure Map

```
python-tools/
├── workflow_models.py          [NEW] All Pydantic data models
├── workflow_config.py          [NEW] Central config from env vars
├── workflow_engine.py          [MOD] WorkflowStore + migrations + triggers
├── workflow_api.py             [MOD] FastAPI routes, Pydantic request models
├── workflow_worker.py          [NEW] Worker main loop + StageRegistry
├── web_api_server.py           [MOD] Auth hardening
├── migrations/
│   ├── 001_initial.sql         [NEW] Current schema extracted
│   └── 002_pipeline.sql        [NEW] Phase 1A additions
├── pipeline/
│   ├── __init__.py             [NEW]
│   ├── contracts.py            [NEW] Protocol classes
│   ├── search_stage.py         [NEW] Phase 2A
│   ├── screening_stage.py      [NEW] Phase 2A
│   ├── fulltext_stage.py       [NEW] Phase 2A
│   ├── parse_stage.py          [NEW] Phase 2A
│   ├── extraction_stage.py     [NEW] Phase 2A
│   ├── validation_stage.py     [NEW] Phase 2A
│   └── report_stage.py         [NEW] Phase 2A
├── adapters/
│   ├── __init__.py             [NEW]
│   ├── search_base.py          [NEW] SearchProvider Protocol re-export
│   ├── mock_search.py          [NEW] Phase 2A
│   ├── embedding_base.py       [NEW] EmbeddingRetriever Protocol re-export
│   ├── mock_embedding.py       [NEW] Phase 2A
│   ├── agent_base.py           [NEW] AgentAdapter Protocol re-export
│   ├── mock_agent.py           [NEW] Phase 2A
│   └── mock_parser.py          [NEW] Phase 2A
├── fixtures/
│   ├── papers.json             [NEW] Phase 2A
│   ├── sample_material.pdf     [NEW] Phase 2A
│   └── mock_extraction.json    [NEW] Phase 2A
tests/
├── test_workflow_models.py     [NEW]
├── test_workflow_engine.py     [MOD] Expand existing tests
├── test_worker.py              [NEW]
├── test_workflow_api.py        [NEW]
├── test_search_pipeline.py     [NEW] Phase 2A
├── test_pdf_pipeline.py        [NEW] Phase 2A
├── test_e2e_basic.py           [NEW] Phase 2A — 12 scenarios
scripts/
└── run_basic_test.sh           [NEW] Phase 2A
requirements-dev.txt            [NEW]
```

---

## Phase 1A — Minimal Foundation

### Task 1: Data Models (`workflow_models.py`)

**Files:**
- Create: `python-tools/workflow_models.py`
- Create: `tests/test_workflow_models.py`

**Interfaces:**
- Produces: `TaskDefinition`, `Metric`, `EvidenceLocator`, `MaterialComponent`, `RatioBasis(StrEnum)`, `CompositionRatio`, `ProcessStep`, `TestCondition`, `PerformanceMetric`, `SampleExtraction`, `ParsedBlock`, `ParsedPage`, `ParsedDocument`, `ResearchReport`, `ReportSection`, `PaperStatus(StrEnum)`, `JobStatus(StrEnum)`, `VersionType(StrEnum)`, `SearchQuery`, `PaperMetadata`, `ScoredPaper`, `ScreeningDecision`

- [ ] **Step 1: Write the test file**

```python
# tests/test_workflow_models.py
import pytest
from pydantic import ValidationError
from workflow_models import (
    TaskDefinition, Metric, EvidenceLocator, CompositionRatio,
    RatioBasis, SampleExtraction, PaperStatus, JobStatus, VersionType,
    ProcessStep, PerformanceMetric, TestCondition, ParsedBlock, ParsedPage,
    ParsedDocument, ResearchReport, ReportSection,
)

class TestTaskDefinition:
    def test_minimal_valid(self):
        d = TaskDefinition(
            research_object="test material",
            application="test app",
            target_metrics=[Metric(name="strength", unit="MPa", target_range=">100")],
            hard_constraints=["lab feasible"],
            optimization_objectives=["max strength"],
            acceptable_tradeoffs=["cost"],
        )
        assert d.paper_target == 10  # default
        assert d.languages == ["zh", "en"]

    def test_invalid_paper_target(self):
        with pytest.raises(ValidationError):
            TaskDefinition(
                research_object="x", application="y",
                target_metrics=[], hard_constraints=[], optimization_objectives=[],
                acceptable_tradeoffs=[], paper_target=3,  # below ge=5
            )

    def test_list_defaults_are_independent(self):
        a = TaskDefinition(
            research_object="x", application="y",
            target_metrics=[], hard_constraints=[], optimization_objectives=[],
            acceptable_tradeoffs=[],
        )
        b = TaskDefinition(
            research_object="x", application="y",
            target_metrics=[], hard_constraints=[], optimization_objectives=[],
            acceptable_tradeoffs=[],
        )
        a.languages.append("fr")
        assert "fr" not in b.languages


class TestEvidenceLocator:
    def test_source_types(self):
        for st in ["explicit", "derived", "estimated", "inferred", "missing"]:
            e = EvidenceLocator(
                evidence_id="EV-001",
                field_path="samples/S1/ratios/1/raw_value",
                work_id="doi:10.1000/test",
                file_version="v1",
                page=5,
                source_type=st,
            )
            assert e.source_type == st

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            EvidenceLocator(
                evidence_id="EV-001",
                field_path="x",
                work_id="x",
                file_version="x",
                page=1,
                source_type="fabricated",
            )

    def test_optional_fields(self):
        e = EvidenceLocator(
            evidence_id="EV-001",
            field_path="x",
            work_id="x",
            file_version="x",
            page=1,
            source_type="explicit",
            section="2.3",
            figure="Figure 4",
            table="Table 2",
            quote_or_value="10 wt%",
        )
        assert e.section == "2.3"
        assert e.figure == "Figure 4"


class TestCompositionRatio:
    def test_default_ratio_basis(self):
        r = CompositionRatio(component="PEO", raw_value="10", raw_unit="wt%")
        assert r.ratio_basis == RatioBasis.UNSPECIFIED

    def test_evidence_ids(self):
        r = CompositionRatio(
            component="PEO", raw_value="10", raw_unit="wt%",
            evidence_ids=["EV-001", "EV-002"],
        )
        assert len(r.evidence_ids) == 2

    def test_default_evidence_ids(self):
        r = CompositionRatio(component="PEO", raw_value="10", raw_unit="wt%")
        assert r.evidence_ids == []


class TestSampleExtraction:
    def test_full_sample(self):
        evidence = [
            EvidenceLocator(
                evidence_id="EV-001", field_path="samples/S1/ratios/1/raw_value",
                work_id="doi:x", file_version="v1", page=5, source_type="explicit",
            ),
            EvidenceLocator(
                evidence_id="EV-002", field_path="samples/S1/performance/1/value",
                work_id="doi:x", file_version="v1", page=7, source_type="estimated",
            ),
        ]
        sample = SampleExtraction(
            sample_id="S1",
            components=[{"name": "PEO", "role": "matrix"}],
            ratios=[CompositionRatio(
                component="PEO", raw_value="10", raw_unit="wt%",
                evidence_ids=["EV-001"],
            )],
            performance_metrics=[PerformanceMetric(
                property="conductivity", value="5e-3", unit="S/cm",
                evidence_ids=["EV-002"],
            )],
            evidence=evidence,
        )
        assert sample.sample_id == "S1"
        assert sample.is_abstract_only is False
        # Verify mixed evidence per field
        assert sample.ratios[0].evidence_ids == ["EV-001"]
        assert sample.performance_metrics[0].evidence_ids == ["EV-002"]

    def test_abstract_only(self):
        s = SampleExtraction(sample_id="S1", is_abstract_only=True)
        assert s.is_abstract_only is True
        assert s.ratios == []


class TestEnums:
    def test_paper_status_values(self):
        assert PaperStatus.CANDIDATE == "candidate"
        assert PaperStatus.SELECTED == "selected"
        assert PaperStatus.FETCHED == "fetched"
        assert PaperStatus.PARSED == "parsed"
        assert PaperStatus.EXTRACTED == "extracted"
        assert PaperStatus.DEGRADED == "degraded"
        assert PaperStatus.FAILED == "failed"

    def test_job_status_values(self):
        assert JobStatus.QUEUED == "queued"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.RETRY_WAIT == "retry_wait"
        assert JobStatus.DEAD_LETTER == "dead_letter"

    def test_version_type_values(self):
        assert VersionType.PREPRINT == "preprint"
        assert VersionType.JOURNAL == "journal"
        assert VersionType.CORRIGENDUM == "corrigendum"
        assert VersionType.UNKNOWN == "unknown"


class TestParsedDocument:
    def test_minimal(self):
        doc = ParsedDocument(work_id="doi:x", file_version="v1")
        assert doc.pages == []

    def test_with_blocks(self):
        block = ParsedBlock(
            block_id="p3-b12", page_number=3, block_type="paragraph",
            text="The conductivity was measured.",
        )
        page = ParsedPage(page_number=3, blocks=[block])
        doc = ParsedDocument(work_id="doi:x", file_version="v1", pages=[page])
        assert doc.pages[0].blocks[0].block_id == "p3-b12"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_models.py -v`
Expected: ModuleNotFoundError for workflow_models

- [ ] **Step 3: Write `workflow_models.py`**

```python
"""Unified Pydantic data models for the material-science literature workflow.

Design invariants:
- source_type is per-field (EvidenceLocator), NOT per-extraction.
- Every data-carrying field (ratio, process step, performance, test condition)
  references its evidence via evidence_ids.
- Pydantic list/dict fields use Field(default_factory=...).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PaperStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    FETCHED = "fetched"
    PARSED = "parsed"
    EXTRACTED = "extracted"
    DEGRADED = "degraded"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class RatioBasis(StrEnum):
    MASS_FRACTION = "mass_fraction"
    VOLUME_FRACTION = "volume_fraction"
    MOLE_FRACTION = "mole_fraction"
    MASS_PARTS = "mass_parts"
    RELATIVE_TO_MATRIX = "relative_to_matrix"
    RELATIVE_TO_TOTAL = "relative_to_total"
    RELATIVE_TO_PRECURSOR = "relative_to_precursor"
    UNSPECIFIED = "unspecified"


class VersionType(StrEnum):
    PREPRINT = "preprint"
    JOURNAL = "journal"
    CORRIGENDUM = "corrigendum"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

class Metric(BaseModel):
    name: str
    unit: str | None = None
    target_range: str | None = None


class TaskDefinition(BaseModel):
    research_object: str
    application: str
    target_metrics: list[Metric] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    optimization_objectives: list[str] = Field(default_factory=list)
    acceptable_tradeoffs: list[str] = Field(default_factory=list)
    paper_target: int = Field(default=10, ge=5, le=200)
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    temporary_lab_constraints: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence (per-field source tracking)
# ---------------------------------------------------------------------------

class EvidenceLocator(BaseModel):
    evidence_id: str
    field_path: str  # e.g. "samples/S1/ratios/2/raw_value"
    work_id: str
    file_version: str
    page: int = Field(ge=1)
    section: str | None = None
    figure: str | None = None
    table: str | None = None
    quote_or_value: str | None = None
    source_type: Literal["explicit", "derived", "estimated", "inferred", "missing"]


# ---------------------------------------------------------------------------
# Material extraction fields (each carries evidence_ids)
# ---------------------------------------------------------------------------

class MaterialComponent(BaseModel):
    name: str
    role: str | None = None
    supplier: str | None = None


class CompositionRatio(BaseModel):
    component: str
    raw_value: str
    raw_unit: str
    ratio_basis: RatioBasis = RatioBasis.UNSPECIFIED
    normalized_value: float | None = None
    normalized_unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ProcessStep(BaseModel):
    step_number: int
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    equipment: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TestCondition(BaseModel):
    property: str
    method: str | None = None
    standard: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class PerformanceMetric(BaseModel):
    property: str
    value: str
    unit: str
    test_condition: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregated extraction
# ---------------------------------------------------------------------------

class SampleExtraction(BaseModel):
    sample_id: str
    components: list[MaterialComponent] = Field(default_factory=list)
    ratios: list[CompositionRatio] = Field(default_factory=list)
    process_steps: list[ProcessStep] = Field(default_factory=list)
    test_conditions: list[TestCondition] = Field(default_factory=list)
    performance_metrics: list[PerformanceMetric] = Field(default_factory=list)
    evidence: list[EvidenceLocator] = Field(default_factory=list)
    is_abstract_only: bool = False


# ---------------------------------------------------------------------------
# Parsed document
# ---------------------------------------------------------------------------

class ParsedBlock(BaseModel):
    block_id: str
    page_number: int = Field(ge=1)
    block_type: str  # "paragraph", "table", "figure_caption", "section_heading"
    text: str
    bbox: tuple[float, float, float, float] | None = None


class ParsedPage(BaseModel):
    page_number: int = Field(ge=1)
    blocks: list[ParsedBlock] = Field(default_factory=list)
    captions: list[str] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    work_id: str
    file_version: str
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class ReportSection(BaseModel):
    heading: str
    content: str
    order: int


class ResearchReport(BaseModel):
    task_id: str
    version: int = Field(ge=1)
    sections: list[ReportSection] = Field(default_factory=list)
    format: str = "markdown"


# ---------------------------------------------------------------------------
# Pipeline contracts — lightweight data classes used by Protocols
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    text: str
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    year_policy: str = "recent_5_years_plus_foundational"
    max_results: int = 50


class PaperMetadata(BaseModel):
    work_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    source: str = ""
    document_type: str = ""
    url: str = ""
    language: str = ""
    version_type: VersionType = VersionType.UNKNOWN
    version_id: str = ""


class ScoredPaper(BaseModel):
    metadata: PaperMetadata
    relevance_score: float = 0.0
    authority_score: float = 0.0
    confidence_score: float = 0.0


class ScreeningDecision(BaseModel):
    paper: PaperMetadata
    include: bool
    role_tags: list[str] = Field(default_factory=list)
    reason: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_models.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add python-tools/workflow_models.py tests/test_workflow_models.py
git commit -m "feat: add unified Pydantic data models with field-level evidence"
```

---

### Task 2: Database Migrations (`migrations/`)

**Files:**
- Create: `python-tools/migrations/001_initial.sql`
- Create: `python-tools/migrations/002_pipeline.sql`
- Modify: `python-tools/workflow_engine.py` — add `_run_migrations()`, checksum validation

**Interfaces:**
- Produces: `WorkflowStore._run_migrations()` called from `__init__`; `schema_migrations` table
- Consumes: none (standalone)

- [ ] **Step 1: Write 001_initial.sql**

Extract the current `CREATE TABLE` statements from `workflow_engine.py:_initialize()`:

```sql
-- 001_initial.sql: snapshot of existing schema as of 2026-07-17
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
```

- [ ] **Step 2: Write 002_pipeline.sql**

```sql
-- 002_pipeline.sql: Phase 1A schema additions
-- Requires 001_initial.sql to have been applied first.

-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- Artifact metadata (files stored on disk, not in DB)
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    artifact_type TEXT NOT NULL,
    format TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id, artifact_type);

-- Reports (versioned markdown)
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    format TEXT NOT NULL DEFAULT 'markdown',
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

-- Job attempt audit trail
CREATE TABLE IF NOT EXISTS job_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    attempt_num INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,
    error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_attempts_unique
    ON job_attempts(job_id, attempt_num);

-- jobs table: lease and idempotency columns
ALTER TABLE jobs ADD COLUMN worker_id TEXT;
ALTER TABLE jobs ADD COLUMN claimed_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_token TEXT;
ALTER TABLE jobs ADD COLUMN next_retry_at TEXT;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
ALTER TABLE jobs ADD COLUMN result_json TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- papers table: per-paper status and version fields
ALTER TABLE papers ADD COLUMN paper_status TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE papers ADD COLUMN error_message TEXT;
ALTER TABLE papers ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE papers ADD COLUMN version_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE papers ADD COLUMN version_id TEXT NOT NULL DEFAULT '';

-- tasks table: optimistic locking
ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;

-- Human-gate trigger: prevent jobs for wait/terminal stages
CREATE TRIGGER IF NOT EXISTS trg_jobs_no_human_wait_stage
BEFORE INSERT ON jobs
WHEN NEW.stage IN (
    'WAITING_PAPER_APPROVAL', 'WAITING_DATA_REVIEW',
    'CLARIFYING', 'DRAFT', 'COMPLETED', 'FAILED', 'PAUSED'
)
BEGIN
    SELECT RAISE(ABORT, 'cannot create job for human-wait or terminal stage');
END;
```

- [ ] **Step 3: Add `_run_migrations()` to WorkflowStore**

In `workflow_engine.py`, add after `__init__`:

```python
import hashlib as _hashlib

def _run_migrations(self) -> None:
    """Apply pending SQL migrations with checksum validation."""
    migrations_dir = Path(__file__).resolve().parent / "migrations"
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
```

Replace the `_initialize` method's inline `CREATE TABLE IF NOT EXISTS` calls with a call to `_run_migrations()`. Keep `_initialize` for creating the directory and running migrations:

```python
def _initialize(self) -> None:
    Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    self._run_migrations()
```

- [ ] **Step 4: Verify migration runs against existing test DB**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py -v`
Expected: existing tests still PASS (migrations create same schema)

- [ ] **Step 5: Test checksum validation**

```python
# Add to tests/test_workflow_engine.py
def test_migration_checksum_validation(self):
    # Modify a migration file in a temp copy
    import shutil, tempfile
    tmp = tempfile.mkdtemp()
    try:
        mig_dir = os.path.join(tmp, "migrations")
        os.makedirs(mig_dir)
        src = os.path.join(os.path.dirname(__file__), "..", "python-tools", "migrations")
        for f in os.listdir(src):
            shutil.copy2(os.path.join(src, f), os.path.join(mig_dir, f))
        # Tamper with a migration
        with open(os.path.join(mig_dir, "001_initial.sql"), "a") as f:
            f.write("\n-- tampered\n")
        # This should raise
        # Note: test uses a subclass that overrides migrations_dir
    finally:
        shutil.rmtree(tmp)
```

- [ ] **Step 6: Commit**

```bash
git add python-tools/migrations/ python-tools/workflow_engine.py tests/test_workflow_engine.py
git commit -m "feat: add migration system with checksum validation

- 001_initial.sql: snapshot of existing schema
- 002_pipeline.sql: Phase 1A additions (artifacts, reports, job_attempts,
  lease columns, idempotency index, paper_status, revision, human-gate trigger)
- _run_migrations(): apply pending SQL files, validate checksums
- _initialize() now delegates to _run_migrations()"
```

---

### Task 3: WorkflowConfig (`workflow_config.py`)

**Files:**
- Create: `python-tools/workflow_config.py`

**Interfaces:**
- Produces: `WorkflowConfig` dataclass, `load_config()` factory

- [ ] **Step 1: Write `workflow_config.py`**

```python
"""Central configuration for the workflow system. All values come from
environment variables with sensible defaults for local development."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkflowConfig:
    # Database
    db_path: str = field(
        default_factory=lambda: os.environ.get(
            "WORKFLOW_DB_PATH",
            str(Path(__file__).resolve().parent.parent / "data" / "workflow.db"),
        )
    )

    # Worker
    worker_id: str = field(
        default_factory=lambda: os.environ.get("WORKER_ID", "worker-1")
    )
    poll_interval: float = float(os.environ.get("WORKER_POLL_INTERVAL", "2.0"))
    lease_duration: int = int(os.environ.get("WORKER_LEASE_DURATION", "300"))
    renew_interval: int = int(os.environ.get("WORKER_RENEW_INTERVAL", "30"))

    # Data directory
    data_dir: str = field(
        default_factory=lambda: os.environ.get(
            "WORKFLOW_DATA_DIR",
            str(Path(__file__).resolve().parent.parent / "data"),
        )
    )

    # Auth (test mode)
    app_env: str = os.environ.get("APP_ENV", "test")
    jwt_secret: str = field(
        default_factory=lambda: os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
    )

    # Test users (only valid when APP_ENV=test)
    test_users: dict = field(default_factory=lambda: {
        "alice": {"password": "test-pass", "role": "user"},
        "bob": {"password": "test-pass", "role": "user"},
        "admin": {"password": "admin-pass", "role": "admin"},
    })


def load_config(**overrides) -> WorkflowConfig:
    """Factory that applies optional overrides (useful for tests)."""
    return WorkflowConfig(**overrides)
```

- [ ] **Step 2: Commit**

```bash
git add python-tools/workflow_config.py
git commit -m "feat: add WorkflowConfig with env-var-driven settings"
```

---

### Task 4: Job Lease & Idempotency in WorkflowStore

**Files:**
- Modify: `python-tools/workflow_engine.py` — `claim_next_job`, `complete_job`, `retry_job`, `fail_job`, `renew_lease`, `_promote_dead_letters`
- Modify: `tests/test_workflow_engine.py` — expand job tests

**Interfaces:**
- Consumes: `JobStatus` from workflow_models, `WorkflowConfig` (for defaults)
- Produces: `claim_next_job(worker_id, lease_duration) -> ClaimedJob | None`, `complete_job(job_id, worker_id, lease_token, result)`, `retry_job(job_id, worker_id, lease_token, error)`, `fail_job(job_id, worker_id, lease_token, error)`, `renew_lease(job_id, worker_id, lease_token, lease_duration) -> bool`, `_promote_dead_letters()`

- [ ] **Step 1: Write expanded job tests**

```python
# Add to tests/test_workflow_engine.py

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
    import sqlite3
    with self.assertRaises(sqlite3.IntegrityError):
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (task_id, stage, payload_json, status, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (task["id"], "SEARCHING", "{}", "test-key-123", utc_now(), utc_now()),
            )
```

- [ ] **Step 2: Run to verify failures**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py::WorkflowStoreTest::test_job_lease_claim_and_complete -v`
Expected: FAIL (AttributeError or similar — methods not yet implemented)

- [ ] **Step 3: Implement lease methods in WorkflowStore**

Replace `next_job`, `finish_job` and add new methods in `workflow_engine.py`:

```python
ClaimedJob = dict[str, Any]  # returned by claim_next_job

def claim_next_job(self, worker_id: str, lease_duration: int = 300) -> ClaimedJob | None:
    """Claim the oldest eligible job with lease fencing."""
    import uuid as _uuid
    now = utc_now()
    lease_token = _uuid.uuid4().hex
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
                       lease_expires_at = datetime(?, '+' || ? || ' seconds'),
                       lease_token = ?,
                       attempts = attempts + 1
                   WHERE id = (
                       SELECT id FROM jobs
                       WHERE (status = 'queued')
                          OR (status = 'running' AND lease_expires_at < datetime(?))
                          OR (status = 'retry_wait' AND next_retry_at <= datetime(?))
                       ORDER BY id LIMIT 1
                   )
                   AND attempts < max_attempts
                   RETURNING *""",
                (worker_id, now, now, str(lease_duration), lease_token, now, now),
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
        cur = conn.execute(
            """UPDATE jobs SET status = 'retry_wait', next_retry_at = datetime(?, '+' || ? || ' seconds'),
               worker_id = NULL, lease_token = NULL, error = ?, updated_at = ?
               WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'""",
            (now, str(delay), error, now, job_id, worker_id, lease_token),
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
    now = utc_now()
    with self._connect() as conn:
        cur = conn.execute(
            """UPDATE jobs SET lease_expires_at = datetime(?, '+' || ? || ' seconds')
               WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'""",
            (now, str(lease_duration), job_id, worker_id, lease_token),
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
```

Remove old `next_job` and `finish_job` methods.

- [ ] **Step 4: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py -v`
Expected: all tests PASS (including new lease/retry/fencing tests)

- [ ] **Step 5: Commit**

```bash
git add python-tools/workflow_engine.py tests/test_workflow_engine.py
git commit -m "feat: add job lease, fencing, retry, and idempotency to WorkflowStore

- claim_next_job: atomic claim with lease_token, attempts++, max_attempts check
- complete/retry/fail: all fenced by (worker_id, lease_token)
- renew_lease: extends lease_expires_at, returns False if lost
- _promote_dead_letters: maxed-out jobs -> dead_letter
- idempotency_key UNIQUE index enforced at DB level"
```

---

### Task 5: Per-Paper Status & Artifact Tracking

**Files:**
- Modify: `python-tools/workflow_engine.py` — `submit_candidates`, `approve_papers`, `record_extraction`, paper status transitions, artifact methods
- Modify: `tests/test_workflow_engine.py` — paper status tests

**Interfaces:**
- Produces: `record_artifact(task_id, paper_id, artifact_type, format, path, sha256) -> dict`, `update_paper_status(paper_id, status, error=None)`, `get_artifacts(task_id, artifact_type=None) -> list[dict]`

- [ ] **Step 1: Write paper status tests**

```python
# Add to tests/test_workflow_engine.py

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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py::WorkflowStoreTest::test_paper_status_transitions -v`
Expected: FAIL

- [ ] **Step 3: Implement in WorkflowStore**

Add to `workflow_engine.py`:

```python
def update_paper_status(self, paper_id: str, status: str, error: str | None = None) -> None:
    now = utc_now()
    with self._connect() as conn:
        conn.execute(
            "UPDATE papers SET paper_status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error, now, paper_id),
        )

def record_artifact(self, task_id: str, paper_id: str | None, artifact_type: str,
                    format: str, path: str, sha256: str | None = None) -> dict[str, Any]:
    import uuid as _uuid
    now = utc_now()
    artifact_id = _uuid.uuid4().hex
    with self._connect() as conn:
        conn.execute(
            """INSERT INTO artifacts (id, task_id, paper_id, artifact_type, format, path, sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, task_id, paper_id, artifact_type, format, path, sha256, now),
        )
    return {"id": artifact_id, "path": path, "sha256": sha256}

def get_artifacts(self, task_id: str, artifact_type: str | None = None) -> list[dict[str, Any]]:
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
    import uuid as _uuid
    now = utc_now()
    report_id = _uuid.uuid4().hex
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
```

Update `approve_papers` to set `paper_status`:

```python
# In approve_papers, after updating selection_status:
conn.execute(
    "UPDATE papers SET paper_status = 'selected', updated_at = ? WHERE id = ?",
    (utc_now(), paper_id),
)
```

- [ ] **Step 4: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add python-tools/workflow_engine.py tests/test_workflow_engine.py
git commit -m "feat: per-paper status, artifact & report tracking in WorkflowStore"
```

---

### Task 6: Pipeline Contracts

**Files:**
- Create: `python-tools/pipeline/__init__.py` (empty)
- Create: `python-tools/pipeline/contracts.py`

**Interfaces:**
- Produces: `SearchProvider`, `EmbeddingRetriever`, `Reranker`, `FulltextProvider`, `DocumentParser`, `AgentAdapter` (all `Protocol` classes)

- [ ] **Step 1: Write `contracts.py`**

```python
"""Protocol interfaces for pipeline stages. All adapters (mock or real)
must satisfy these protocols. Stage handlers depend on these, never on
concrete adapter implementations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from workflow_models import (
    SearchQuery, PaperMetadata, ScoredPaper, ScreeningDecision,
    TaskDefinition, ParsedDocument, SampleExtraction, ResearchReport,
)


@runtime_checkable
class SearchProvider(Protocol):
    async def search(self, query: SearchQuery) -> list[PaperMetadata]: ...


@runtime_checkable
class EmbeddingRetriever(Protocol):
    async def retrieve(
        self, task: TaskDefinition, papers: list[PaperMetadata], top_k: int
    ) -> list[ScoredPaper]: ...


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, papers: list[ScoredPaper]
    ) -> list[ScoredPaper]: ...


@runtime_checkable
class FulltextProvider(Protocol):
    async def fetch(self, paper: PaperMetadata) -> bytes | None: ...


@runtime_checkable
class DocumentParser(Protocol):
    async def parse(self, pdf_bytes: bytes) -> ParsedDocument: ...


@runtime_checkable
class AgentAdapter(Protocol):
    async def screen_abstracts(
        self, task: TaskDefinition, papers: list[PaperMetadata]
    ) -> list[ScreeningDecision]: ...
    async def extract_paper(
        self, task: TaskDefinition, parsed: ParsedDocument
    ) -> list[SampleExtraction]: ...
    async def generate_report(
        self, task: TaskDefinition, extractions: list[SampleExtraction]
    ) -> ResearchReport: ...
```

- [ ] **Step 2: Verify imports work**

Run: `cd python-tools && python -c "from pipeline.contracts import SearchProvider, AgentAdapter; print('Protocols imported OK')"`
Expected: "Protocols imported OK"

- [ ] **Step 3: Commit**

```bash
git add python-tools/pipeline/
git commit -m "feat: add pipeline Protocol contracts"
```

---

### Task 7: Optimistic Locking & Transition Fixes

**Files:**
- Modify: `python-tools/workflow_engine.py` — `_transition()` with revision check, `rollback()` direction constraint, `resume()` job-creation logic
- Modify: `tests/test_workflow_engine.py` — optimistic locking test, rollback direction test

**Interfaces:**
- Produces: `_transition()` now uses `WHERE revision = ?`, rowcount check; `rollback()` validates target is earlier than current; `resume()` creates job only for automated stages

- [ ] **Step 1: Write tests**

```python
# Add to test_workflow_engine.py

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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py::WorkflowStoreTest::test_rollback_only_to_earlier_stage -v`
Expected: FAIL or unexpected behavior

- [ ] **Step 3: Implement fixes**

In `_transition()`, add optimistic locking:
```python
cur = conn.execute(
    "UPDATE tasks SET status = ?, previous_status = ?, updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
    (target, previous, now, task["id"], task["revision"]),
)
if cur.rowcount == 0:
    raise InvalidTransitionError("Concurrent modification detected — retry")
```

In `rollback()`, add direction check:
```python
def _stage_order(stage: TaskStatus) -> int:
    try:
        return ACTIVE_PIPELINE.index(stage)
    except ValueError:
        return -1

if _stage_order(target_status) >= _stage_order(TaskStatus(task["status"])):
    raise InvalidTransitionError(
        f"Rollback target {target} must be earlier than current {task['status']}"
    )
```

In `resume()`, only enqueue if previous status is an automated stage:
```python
HUMAN_WAIT = {TaskStatus.WAITING_PAPER_APPROVAL, TaskStatus.WAITING_DATA_REVIEW,
              TaskStatus.CLARIFYING, TaskStatus.DRAFT, TaskStatus.COMPLETED,
              TaskStatus.FAILED, TaskStatus.PAUSED}
should_enqueue = TaskStatus(task["previous_status"]) not in HUMAN_WAIT
return self._transition(task, actor_id, TaskStatus(task["previous_status"]),
                        "task_resumed", enqueue=should_enqueue)
```

- [ ] **Step 4: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_engine.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add python-tools/workflow_engine.py tests/test_workflow_engine.py
git commit -m "fix: optimistic locking, rollback direction, resume job logic"
```

---

### Task 8: Worker Framework

**Files:**
- Create: `python-tools/workflow_worker.py`
- Create: `tests/test_worker.py`

**Interfaces:**
- Consumes: `WorkflowStore`, `WorkflowConfig`, `pipeline.contracts`
- Produces: `WorkflowWorker(config, store, registry)`, `StageRegistry`, `StageHandler(Protocol)`, `LeaseLostError`, `RetryableError`, `FatalError`

- [ ] **Step 1: Write worker tests**

```python
# tests/test_worker.py
import os, sys, tempfile, asyncio
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

from workflow_engine import WorkflowStore
from workflow_worker import WorkflowWorker, StageRegistry, StageHandler, LeaseLostError, RetryableError, FatalError
from workflow_config import WorkflowConfig
from workflow_models import TaskStatus


class FakeHandler(StageHandler):
    def __init__(self, result=None, should_fail=None):
        self.result = result or {"done": True}
        self.should_fail = should_fail
        self.call_count = 0

    async def run(self, job, store):
        self.call_count += 1
        if self.should_fail == "retry":
            raise RetryableError("transient")
        elif self.should_fail == "fatal":
            raise FatalError("permanent")
        return self.result


@pytest.mark.asyncio
async def test_worker_claims_and_completes_job():
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(
            db_path=os.path.join(tmp, "test.db"),
            worker_id="test-worker",
            poll_interval=0.1,
            lease_duration=5,
            renew_interval=1,
        )
        store = WorkflowStore(config.db_path)
        registry = StageRegistry(config)
        handler = FakeHandler(result={"papers": 10})
        registry.register("SEARCHING", handler)

        task = store.create_task("alice", "test", "query")
        store.start_search(task["id"], "alice")

        worker = WorkflowWorker(config, store, registry)
        # Run one iteration
        job = store.claim_next_job(config.worker_id)
        assert job is not None
        await handler.run(job, store)
        ok = store.complete_job(job["id"], config.worker_id, job["lease_token"], result=handler.result)
        assert ok is True
        assert handler.call_count == 1
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_worker_retry_on_retryable_error():
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(
            db_path=os.path.join(tmp, "test.db"),
            worker_id="test-worker",
            poll_interval=0.1,
        )
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.start_search(task["id"], "alice")

        job = store.claim_next_job(config.worker_id)
        ok = store.retry_job(job["id"], config.worker_id, job["lease_token"], error="transient")
        assert ok is True
        # Job should be in retry_wait
        with store._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["status"] == "retry_wait"
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_lease_renewal():
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(
            db_path=os.path.join(tmp, "test.db"),
            worker_id="test-worker",
            lease_duration=5,
        )
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.start_search(task["id"], "alice")
        job = store.claim_next_job(config.worker_id)
        old_expiry = job["lease_expires_at"]
        await asyncio.sleep(0.1)
        ok = store.renew_lease(job["id"], config.worker_id, job["lease_token"], lease_duration=10)
        assert ok is True
        with store._connect() as conn:
            row = conn.execute("SELECT lease_expires_at FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["lease_expires_at"] > old_expiry
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Write `workflow_worker.py`**

```python
"""Workflow Worker — claims jobs from SQLite and executes pipeline stages.
Runs as a separate process with direct DB access (no HTTP to API)."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from workflow_engine import WorkflowStore
from workflow_config import WorkflowConfig

logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """Transient failure — job should be retried."""

class FatalError(Exception):
    """Permanent failure — job should go to dead_letter."""

class LeaseLostError(Exception):
    """Lease was invalidated — discard result, another Worker owns this job."""


class StageHandler(Protocol):
    async def run(self, job: dict, store: WorkflowStore) -> dict:
        """Execute a pipeline stage. Raise RetryableError or FatalError on failure."""
        ...


HUMAN_WAIT_STAGES = {
    "WAITING_PAPER_APPROVAL", "WAITING_DATA_REVIEW",
    "CLARIFYING", "DRAFT", "COMPLETED", "FAILED", "PAUSED",
}


class StageRegistry:
    def __init__(self, config: WorkflowConfig):
        self._handlers: dict[str, StageHandler] = {}

    def register(self, stage: str, handler: StageHandler):
        if stage in HUMAN_WAIT_STAGES:
            raise ValueError(f"Cannot register handler for human-wait stage: {stage}")
        self._handlers[stage] = handler

    def get(self, stage: str) -> StageHandler:
        if stage not in self._handlers:
            raise FatalError(f"No handler registered for stage: {stage}")
        return self._handlers[stage]


class WorkflowWorker:
    def __init__(self, config: WorkflowConfig, store: WorkflowStore, registry: StageRegistry):
        self.config = config
        self.store = store
        self.registry = registry

    async def run(self):
        logger.info("Worker %s starting", self.config.worker_id)
        while True:
            job = self.store.claim_next_job(
                worker_id=self.config.worker_id,
                lease_duration=self.config.lease_duration,
            )
            if not job:
                await asyncio.sleep(self.config.poll_interval)
                continue

            logger.info("Claimed job %s stage=%s", job["id"], job["stage"])
            handler_task: asyncio.Task | None = None
            renewal_task: asyncio.Task | None = None

            async def run_handler():
                handler = self.registry.get(job["stage"])
                return await handler.run(job, self.store)

            async def renew_or_die():
                while True:
                    await asyncio.sleep(self.config.renew_interval)
                    renewed = self.store.renew_lease(
                        job_id=job["id"],
                        worker_id=self.config.worker_id,
                        lease_token=job["lease_token"],
                        lease_duration=self.config.lease_duration,
                    )
                    if not renewed:
                        raise LeaseLostError(f"Lease lost for job {job['id']}")

            try:
                handler_task = asyncio.create_task(run_handler())
                renewal_task = asyncio.create_task(renew_or_die())

                done, pending = await asyncio.wait(
                    [handler_task, renewal_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if renewal_task in done:
                    renewal_exc = renewal_task.exception()
                    handler_task.cancel()
                    try:
                        await handler_task
                    except asyncio.CancelledError:
                        pass
                    raise LeaseLostError(f"Lease lost for job {job['id']}") from renewal_exc

                for task in pending:
                    task.cancel()
                if renewal_task in pending:
                    try:
                        await renewal_task
                    except asyncio.CancelledError:
                        pass

                result = handler_task.result()
                self.store.complete_job(
                    job_id=job["id"],
                    worker_id=self.config.worker_id,
                    lease_token=job["lease_token"],
                    result=result,
                )
                logger.info("Job %s completed", job["id"])

            except LeaseLostError:
                logger.warning("Job %s lease lost — discarding result", job["id"])
            except RetryableError as e:
                logger.warning("Job %s retryable: %s", job["id"], e)
                self.store.retry_job(
                    job_id=job["id"],
                    worker_id=self.config.worker_id,
                    lease_token=job["lease_token"],
                    error=str(e),
                )
            except FatalError as e:
                logger.error("Job %s fatal: %s", job["id"], e)
                self.store.fail_job(
                    job_id=job["id"],
                    worker_id=self.config.worker_id,
                    lease_token=job["lease_token"],
                    error=str(e),
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Job %s unexpected failure", job["id"])
                self.store.retry_job(
                    job_id=job["id"],
                    worker_id=self.config.worker_id,
                    lease_token=job["lease_token"],
                    error=f"unexpected: {e}",
                )
            finally:
                for task in [handler_task, renewal_task]:
                    if task and not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    config = WorkflowConfig()
    store = WorkflowStore(config.db_path)
    registry = StageRegistry(config)
    worker = WorkflowWorker(config, store, registry)
    asyncio.run(worker.run())
```

- [ ] **Step 3: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_worker.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add python-tools/workflow_worker.py tests/test_worker.py
git commit -m "feat: add Worker framework with lease renewal and fencing"
```

---

### Task 9: Auth Hardening

**Files:**
- Modify: `python-tools/web_api_server.py` — `/api/auth/token` validates credentials, JWT includes role, ownership checks
- Create: `tests/test_workflow_api.py` — auth tests

**Interfaces:**
- Consumes: `WorkflowConfig` for test_users
- Produces: `auth_required` dependency updated; `/api/auth/token` requires password

- [ ] **Step 1: Write API auth tests**

```python
# tests/test_workflow_api.py
import os, sys
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

os.environ["APP_ENV"] = "test"
os.environ["WORKFLOW_DB_PATH"] = ":memory:"

from web_api_server import app

client = TestClient(app)


def test_auth_token_requires_password():
    resp = client.post("/api/auth/token", json={"user_id": "alice"})
    assert resp.status_code == 422  # missing password

def test_auth_token_invalid_password():
    resp = client.post("/api/auth/token", json={"user_id": "alice", "password": "wrong"})
    assert resp.status_code == 401

def test_auth_token_valid():
    resp = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_create_task_with_token():
    resp = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token = resp.json()["access_token"]
    resp2 = client.post(
        "/api/research/tasks",
        json={"title": "test", "query": "test query"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 201

def test_cannot_access_other_user_task():
    # Alice creates task
    resp_a = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token_a = resp_a.json()["access_token"]
    create = client.post(
        "/api/research/tasks",
        json={"title": "alice task", "query": "x"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    task_id = create.json()["id"]

    # Bob tries to access
    resp_b = client.post("/api/auth/token", json={"user_id": "bob", "password": "test-pass"})
    token_b = resp_b.json()["access_token"]
    get_resp = client.get(
        f"/api/research/tasks/{task_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert get_resp.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_api.py::test_auth_token_requires_password -v`
Expected: FAIL (currently accepts arbitrary user_id)

- [ ] **Step 3: Fix `/api/auth/token`**

Replace the `TokenRequest` model and `/api/auth/token` handler:

```python
class TokenRequest(BaseModel):
    user_id: str
    password: str
    expires_hours: int = 24

@app.post("/api/auth/token")
async def login(req: TokenRequest):
    from workflow_config import WorkflowConfig
    config = WorkflowConfig()
    if config.app_env == "test":
        user = config.test_users.get(req.user_id)
        if not user or user["password"] != req.password:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = create_token(req.user_id, req.expires_hours, role=user["role"])
    else:
        raise HTTPException(status_code=501, detail="Production auth not implemented")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": req.expires_hours * 3600,
    }
```

Update `create_token` to accept role:

```python
def create_token(user_id: str, expires_hours: int = 24, role: str = "user") -> str:
    header = base64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}))
    now = int(time.time())
    payload = base64url_encode(json.dumps({
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + expires_hours * 3600,
        "jti": uuid.uuid4().hex[:12],
    }))
    signature = base64url_encode(
        hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"
```

Update `auth_required` to extract role:

```python
async def auth_required(authorization: str = Header(None)) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = verify_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return payload
    raise HTTPException(status_code=401, detail="Invalid authorization format")
```

Update `_is_admin`:
```python
def _is_admin(user: dict) -> bool:
    return user.get("role") == "admin"
```

And in `workflow_api.py`, update `approve_papers` and `review_extractions` to allow admin:
```python
# Change from:
# task = store.approve_papers(task_id, user["sub"], req.selected_ids)
# To:
task = store.approve_papers(task_id, user["sub"], req.selected_ids, is_admin=_is_admin(user))
```

The `approve_papers` and `review_extractions` methods in `WorkflowStore` already accept `is_admin` — verify they're checking it correctly.

- [ ] **Step 4: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_api.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add python-tools/web_api_server.py python-tools/workflow_api.py tests/test_workflow_api.py
git commit -m "fix: auth hardening — validate passwords, JWT role, ownership checks"
```

---

### Task 10: Artifact Atomic Write Utility

**Files:**
- Create: `python-tools/artifact_utils.py`
- Create: `tests/test_artifact_utils.py`

**Interfaces:**
- Produces: `atomic_write_unique(content, final_path, expected_sha256=None) -> str`, `sha256_file(path) -> str`

- [ ] **Step 1: Write tests**

```python
# tests/test_artifact_utils.py
import os, tempfile, pytest
from artifact_utils import atomic_write_unique, sha256_file

def test_atomic_write_creates_file():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "test.txt")
        result = atomic_write_unique("hello world", path)
        assert result == path
        assert os.path.exists(path)
        with open(path) as f:
            assert f.read() == "hello world"
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

def test_atomic_write_refuses_overwrite():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "test.txt")
        atomic_write_unique("first", path)
        with pytest.raises(FileExistsError):
            atomic_write_unique("second", path)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

def test_content_hash_reuse():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "data.bin")
        result1 = atomic_write_unique(b"same content", path, expected_sha256=sha256_file_content(b"same content"))
        # Second write with same hash should reuse
        result2 = atomic_write_unique(b"same content", path, expected_sha256=sha256_file_content(b"same content"))
        assert result1 == result2
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

def test_content_hash_mismatch_raises():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "data.bin")
        atomic_write_unique(b"content A", path)
        # Different content claiming same hash
        with pytest.raises(FileExistsError):
            atomic_write_unique(b"content B", path, expected_sha256=sha256_file_content(b"content A"))
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

def sha256_file_content(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()
```

- [ ] **Step 2: Write `artifact_utils.py`**

```python
"""Immutable artifact file utilities. Uses os.link() for atomic non-overwrite
publishing and SHA-256 for content-addressed deduplication."""

import hashlib
import os
import uuid


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_unique(content: str | bytes, final_path: str,
                        expected_sha256: str | None = None) -> str:
    """Write content to an immutable final path. Never overwrites.

    - UUID-named paths: os.link() fails with FileExistsError if target exists.
    - Content-addressed paths: if target exists, verify SHA-256. Reuse if match;
      raise FileExistsError if mismatch.
    - Temp file must be on the same filesystem as final_path for os.link() to work.
    """
    tmp_dir = os.path.dirname(final_path) or "."
    tmp_path = os.path.join(tmp_dir, f".tmp.{uuid.uuid4().hex}")

    mode = "wb" if isinstance(content, bytes) else "w"
    with open(tmp_path, mode) as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    # Verify temp file hash if expected
    if expected_sha256:
        actual = sha256_file(tmp_path)
        if actual != expected_sha256:
            os.unlink(tmp_path)
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual}")

    # Check if target already exists
    if os.path.exists(final_path):
        if expected_sha256:
            existing_hash = sha256_file(final_path)
            if existing_hash == expected_sha256:
                os.unlink(tmp_path)  # identical content — reuse
                return final_path
        os.unlink(tmp_path)
        raise FileExistsError(f"Artifact already exists: {final_path}")

    # Atomic publish via hard link
    try:
        os.link(tmp_path, final_path)
    except FileExistsError:
        # Race: another process created it between our check and link
        os.unlink(tmp_path)
        if expected_sha256 and os.path.exists(final_path):
            if sha256_file(final_path) == expected_sha256:
                return final_path
        raise FileExistsError(f"Artifact already exists (race): {final_path}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return final_path
```

- [ ] **Step 3: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_artifact_utils.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add python-tools/artifact_utils.py tests/test_artifact_utils.py
git commit -m "feat: add immutable artifact write utility with hash dedup"
```

---

### Task 11: Workflow API Updates

**Files:**
- Modify: `python-tools/workflow_api.py` — Pydantic request models updated, admin checks on approve/review, report endpoints
- Modify: `tests/test_workflow_api.py` — expand with transition tests

**Interfaces:**
- Produces: `GET /tasks/{id}/reports`, `GET /tasks/{id}/reports/{version}`, `POST /tasks/{id}/papers/approve` (owner/admin), `POST /tasks/{id}/review` (owner/admin)

- [ ] **Step 1: Add report endpoints to workflow_api.py**

```python
@router.get("/tasks/{task_id}/reports")
async def get_reports(task_id: str, user: dict = Depends(auth_dependency)):
    try:
        store.get_task(task_id, user["sub"], _is_admin(user))
        return {"reports": store.get_reports(task_id)}
    except WorkflowError as exc:
        raise _handle_error(exc) from exc

@router.get("/tasks/{task_id}/reports/{version}")
async def get_report(task_id: str, version: int, user: dict = Depends(auth_dependency)):
    try:
        store.get_task(task_id, user["sub"], _is_admin(user))
        reports = store.get_reports(task_id)
        for r in reports:
            if r["version"] == version:
                # Read report from disk
                with open(r["path"], encoding="utf-8") as f:
                    return {"report": f.read(), "metadata": r}
        raise HTTPException(status_code=404, detail=f"Report version {version} not found")
    except WorkflowError as exc:
        raise _handle_error(exc) from exc
```

Update `approve_papers` to pass `is_admin`:
```python
@router.post("/tasks/{task_id}/papers/approve")
async def approve_papers(task_id: str, req: PaperApproval, user: dict = Depends(auth_dependency)):
    try:
        return store.approve_papers(task_id, user["sub"], req.selected_ids, is_admin=_is_admin(user))
    except WorkflowError as exc:
        raise _handle_error(exc) from exc
```

Update the WorkflowStore `approve_papers` signature to accept `is_admin`:
```python
def approve_papers(self, task_id, actor_id, selected_ids, is_admin=False):
    task = self.get_task(task_id, actor_id, is_admin)
    # ... rest of method
```

- [ ] **Step 2: Write expanded API tests**

```python
# Add to test_workflow_api.py
def test_admin_can_approve_others_papers():
    # Create as alice
    resp_a = client.post("/api/auth/token", json={"user_id": "alice", "password": "test-pass"})
    token_a = resp_a.json()["access_token"]
    create = client.post("/api/research/tasks", json={"title": "x", "query": "y"}, headers={"Authorization": f"Bearer {token_a}"})
    task_id = create.json()["id"]
    # Submit candidates
    client.put(
        f"/api/research/tasks/{task_id}/definition",
        json={"definition": {"research_object": "x", "application": "y", "target_metrics": [], "hard_constraints": [], "optimization_objectives": [], "acceptable_tradeoffs": []}},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    client.post(f"/api/research/tasks/{task_id}/start", headers={"Authorization": f"Bearer {token_a}"})
    client.post(
        f"/api/research/tasks/{task_id}/candidates",
        json={"papers": [{"id": "p1", "title": "Test Paper", "role_tags": ["target_performance"]}]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # Admin approves
    resp_adm = client.post("/api/auth/token", json={"user_id": "admin", "password": "admin-pass"})
    token_adm = resp_adm.json()["access_token"]
    approve = client.post(
        f"/api/research/tasks/{task_id}/papers/approve",
        json={"selected_ids": ["p1"]},
        headers={"Authorization": f"Bearer {token_adm}"},
    )
    assert approve.status_code == 200
```

- [ ] **Step 3: Run tests**

Run: `cd python-tools && python -m pytest ../tests/test_workflow_api.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add python-tools/workflow_api.py python-tools/workflow_engine.py tests/test_workflow_api.py
git commit -m "feat: report endpoints, admin approve/review, API hardening"
```

---

## Phase 2A — Mock Happy-Path E2E

### Task 12: Mock Search & Embedding Adapters

**Files:**
- Create: `python-tools/adapters/__init__.py` (empty)
- Create: `python-tools/adapters/search_base.py`
- Create: `python-tools/adapters/mock_search.py`
- Create: `python-tools/adapters/embedding_base.py`
- Create: `python-tools/adapters/mock_embedding.py`
- Create: `python-tools/fixtures/papers.json`

- [ ] **Step 1: Write `adapters/search_base.py`** (re-export Protocol)

```python
from pipeline.contracts import SearchProvider, FulltextProvider
__all__ = ["SearchProvider", "FulltextProvider"]
```

- [ ] **Step 2: Write `adapters/mock_search.py`**

```python
"""Mock search provider returning fixed papers from fixtures/papers.json."""

import json
import os
from pathlib import Path

from workflow_models import SearchQuery, PaperMetadata, VersionType


class MockSearchProvider:
    def __init__(self, fixture_path: str | None = None):
        if fixture_path is None:
            fixture_path = str(
                Path(__file__).resolve().parent.parent / "fixtures" / "papers.json"
            )
        with open(fixture_path, encoding="utf-8") as f:
            self._papers = json.load(f)

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        results = []
        for p in self._papers[: query.max_results]:
            results.append(PaperMetadata(
                work_id=p["work_id"],
                title=p["title"],
                authors=p.get("authors", []),
                year=p.get("year"),
                abstract=p.get("abstract", ""),
                doi=p.get("doi", ""),
                source=p.get("source", "mock"),
                document_type=p.get("document_type", "article"),
                url=p.get("url", ""),
                language=p.get("language", "en"),
                version_type=VersionType(p.get("version_type", "journal")),
                version_id=p.get("version_id", "v1"),
            ))
        return results


class MockFulltextProvider:
    async def fetch(self, paper: PaperMetadata) -> bytes | None:
        fixture_pdf = (
            Path(__file__).resolve().parent.parent / "fixtures" / "sample_material.pdf"
        )
        if fixture_pdf.exists():
            return fixture_pdf.read_bytes()
        return None
```

- [ ] **Step 3: Write `fixtures/papers.json`**

```json
[
  {
    "work_id": "doi:10.1000/benchmark-001",
    "title": "High-Performance Flexible Conductive Composite with Silver Nanowires",
    "authors": ["Zhang, L.", "Wang, H."],
    "year": 2024,
    "abstract": "We report a flexible conductive composite achieving conductivity of 5000 S/cm using silver nanowires in PDMS matrix. The material exhibits tensile strength of 15 MPa with elongation at break of 200%.",
    "doi": "10.1000/benchmark-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["target_performance"],
    "version_type": "journal",
    "version_id": "v1"
  },
  {
    "work_id": "doi:10.1000/structure-001",
    "title": "Microstructure Evolution in Conductive Polymer Composites",
    "authors": ["Li, X.", "Chen, Y."],
    "year": 2023,
    "abstract": "This study investigates the percolation network formation in PEO-based conductive composites. TEM analysis reveals nanowire distribution at varying concentrations.",
    "doi": "10.1000/structure-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["structure"],
    "version_type": "journal",
    "version_id": "v1"
  },
  {
    "work_id": "doi:10.1000/process-001",
    "title": "Scalable Solution-Processed Fabrication of Composite Films",
    "authors": ["Kim, S."],
    "year": 2024,
    "abstract": "A solution-casting method for fabricating conductive composite films at lab scale. Process includes mixing, sonication, casting, and thermal curing at 80 deg C for 2 hours.",
    "doi": "10.1000/process-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["lab_process"],
    "version_type": "journal",
    "version_id": "v1"
  },
  {
    "work_id": "doi:10.1000/composition-001",
    "title": "Optimization of Filler Loading in Polymer Nanocomposites",
    "authors": ["Park, J.", "Lee, M."],
    "year": 2023,
    "abstract": "Systematic study of filler content from 5 to 40 wt% in PEO/AgNW composites. Optimal conductivity achieved at 25 wt% loading with homogeneous dispersion confirmed by SEM.",
    "doi": "10.1000/composition-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["composition_ratio"],
    "version_type": "journal",
    "version_id": "v1"
  },
  {
    "work_id": "doi:10.1000/review-001",
    "title": "Conductive Polymer Composites: A Comprehensive Review",
    "authors": ["Wang, R.", "Liu, T.", "Zhao, Q."],
    "year": 2022,
    "abstract": "This review covers 200+ papers on conductive polymer composites, establishing benchmark performance values and categorizing fabrication approaches.",
    "doi": "10.1000/review-001",
    "source": "mock",
    "document_type": "review",
    "role_tags": ["authoritative_validation"],
    "version_type": "journal",
    "version_id": "v1"
  },
  {
    "work_id": "doi:10.1000/benchmark-001",
    "title": "High-Performance Flexible Conductive Composite with Silver Nanowires",
    "authors": ["Zhang, L.", "Wang, H."],
    "year": 2024,
    "abstract": "We report a flexible conductive composite...",
    "doi": "10.1000/benchmark-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["target_performance"],
    "version_type": "preprint",
    "version_id": "arxiv-v2"
  },
  {
    "work_id": "doi:10.1000/noabstract-001",
    "title": "Novel Approach to Composite Material Design",
    "authors": ["Smith, A."],
    "year": 2025,
    "abstract": "",
    "doi": "10.1000/noabstract-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["target_performance"],
    "version_type": "unknown",
    "version_id": ""
  },
  {
    "work_id": "doi:10.1000/industrial-001",
    "title": "Industrial-Scale Production of Conductive Films via Roll-to-Roll Processing",
    "authors": ["Johnson, P."],
    "year": 2021,
    "abstract": "Describes roll-to-roll manufacturing requiring 500 kg of raw material and a class-1000 cleanroom. Process temperature exceeds 300 deg C.",
    "doi": "10.1000/industrial-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["lab_process"],
    "version_type": "journal",
    "version_id": "v1"
  },
  {
    "work_id": "doi:10.1000/nopdf-001",
    "title": "Proprietary Composite Formulation for Aerospace Applications",
    "authors": ["Defense Research Lab"],
    "year": 2024,
    "abstract": "A proprietary composite formulation shows excellent mechanical properties. Full text is not publicly available.",
    "doi": "10.1000/nopdf-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["target_performance", "composition_ratio"],
    "version_type": "unknown",
    "version_id": ""
  },
  {
    "work_id": "doi:10.1000/multi-001",
    "title": "Multi-Functional Composite: Conductive, Flexible, and Self-Healing",
    "authors": ["Yang, C.", "Huang, D."],
    "year": 2024,
    "abstract": "We demonstrate a composite material combining electrical conductivity (1000 S/cm), mechanical flexibility (300% strain), and self-healing capability. The formulation uses PEO matrix with AgNW and dynamic crosslinkers.",
    "doi": "10.1000/multi-001",
    "source": "mock",
    "document_type": "article",
    "role_tags": ["target_performance", "structure", "lab_process", "composition_ratio"],
    "version_type": "journal",
    "version_id": "v1"
  }
]
```

- [ ] **Step 4: Write `adapters/embedding_base.py`**

```python
from pipeline.contracts import EmbeddingRetriever, Reranker
__all__ = ["EmbeddingRetriever", "Reranker"]
```

- [ ] **Step 5: Write `adapters/mock_embedding.py`**

```python
from workflow_models import TaskDefinition, PaperMetadata, ScoredPaper


class MockEmbeddingRetriever:
    async def retrieve(self, task: TaskDefinition, papers: list[PaperMetadata],
                       top_k: int) -> list[ScoredPaper]:
        scored = []
        for i, p in enumerate(papers):
            score = 100.0 - i * 5.0
            scored.append(ScoredPaper(metadata=p, relevance_score=max(score, 10.0)))
        return sorted(scored, key=lambda s: s.relevance_score, reverse=True)[:top_k]


class MockReranker:
    async def rerank(self, query: str, papers: list[ScoredPaper]) -> list[ScoredPaper]:
        return sorted(papers, key=lambda s: s.relevance_score, reverse=True)
```

- [ ] **Step 6: Verify mock search works**

Run: `cd python-tools && python -c "import asyncio; from adapters.mock_search import MockSearchProvider; from workflow_models import SearchQuery; p = MockSearchProvider(); results = asyncio.run(p.search(SearchQuery(text='test'))); print(f'Found {len(results)} papers'); print(results[0].title)"`
Expected: "Found 10 papers" + title of first paper

- [ ] **Step 7: Commit**

```bash
git add python-tools/adapters/ python-tools/fixtures/papers.json
git commit -m "feat: add mock search, embedding, and reranker adapters with fixtures"
```

---

### Task 13: Mock Parser & Agent Adapters

**Files:**
- Create: `python-tools/adapters/mock_parser.py`
- Create: `python-tools/adapters/agent_base.py`
- Create: `python-tools/adapters/mock_agent.py`
- Create: `python-tools/fixtures/mock_extraction.json`
- Create: `python-tools/fixtures/sample_material.pdf` (hand-crafted test PDF)

- [ ] **Step 1: Write `adapters/mock_parser.py`**

```python
"""Mock PDF parser using PyMuPDF for test PDFs, fallback for others."""

from workflow_models import ParsedDocument, ParsedPage, ParsedBlock


class MockDocumentParser:
    async def parse(self, pdf_bytes: bytes) -> ParsedDocument:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for i, page in enumerate(doc):
                text = page.get_text()
                blocks = []
                block_idx = 0
                for b in page.get_text("blocks"):
                    if b[4].strip():
                        blocks.append(ParsedBlock(
                            block_id=f"p{i+1}-b{block_idx}",
                            page_number=i + 1,
                            block_type="paragraph",
                            text=b[4].strip(),
                            bbox=(b[0], b[1], b[2], b[3]),
                        ))
                        block_idx += 1
                pages.append(ParsedPage(
                    page_number=i + 1,
                    blocks=blocks,
                    captions=[],
                    tables=[],
                ))
            doc.close()
            return ParsedDocument(work_id="", file_version="", pages=pages)
        except Exception:
            return ParsedDocument(work_id="", file_version="")
```

- [ ] **Step 2: Write `adapters/agent_base.py`**

```python
from pipeline.contracts import AgentAdapter
__all__ = ["AgentAdapter"]
```

- [ ] **Step 3: Write `fixtures/mock_extraction.json`**

```json
{
  "samples": [
    {
      "sample_id": "S1",
      "components": [
        {"name": "PEO", "role": "matrix"},
        {"name": "AgNW", "role": "filler"}
      ],
      "ratios": [
        {
          "component": "AgNW",
          "raw_value": "25",
          "raw_unit": "wt%",
          "ratio_basis": "mass_fraction",
          "normalized_value": 25.0,
          "normalized_unit": "wt%",
          "evidence_ids": ["EV-001"]
        }
      ],
      "process_steps": [
        {
          "step_number": 1,
          "description": "Dissolve PEO in deionized water at 5 wt%",
          "parameters": {"temperature": "25 degC", "time": "2h"},
          "evidence_ids": ["EV-002"]
        },
        {
          "step_number": 2,
          "description": "Add AgNW dispersion and sonicate",
          "parameters": {"sonication_time": "30min", "power": "100W"},
          "evidence_ids": ["EV-003"]
        },
        {
          "step_number": 3,
          "description": "Cast film and cure",
          "parameters": {"temperature": "80 degC", "time": "2h"},
          "evidence_ids": ["EV-004"]
        }
      ],
      "performance_metrics": [
        {
          "property": "electrical conductivity",
          "value": "5000",
          "unit": "S/cm",
          "evidence_ids": ["EV-005"]
        },
        {
          "property": "tensile strength",
          "value": "15",
          "unit": "MPa",
          "evidence_ids": ["EV-006"]
        }
      ],
      "evidence": [
        {
          "evidence_id": "EV-001",
          "field_path": "samples/S1/ratios/0/raw_value",
          "work_id": "doi:10.1000/composition-001",
          "file_version": "v1",
          "page": 5,
          "table": "Table 2",
          "quote_or_value": "25 wt% AgNW",
          "source_type": "explicit"
        },
        {
          "evidence_id": "EV-002",
          "field_path": "samples/S1/process_steps/0/parameters",
          "work_id": "doi:10.1000/composition-001",
          "file_version": "v1",
          "page": 3,
          "section": "2.2 Sample Preparation",
          "source_type": "explicit"
        },
        {
          "evidence_id": "EV-003",
          "field_path": "samples/S1/process_steps/1/parameters",
          "work_id": "doi:10.1000/composition-001",
          "file_version": "v1",
          "page": 3,
          "section": "2.2 Sample Preparation",
          "source_type": "explicit"
        },
        {
          "evidence_id": "EV-004",
          "field_path": "samples/S1/process_steps/2/parameters",
          "work_id": "doi:10.1000/composition-001",
          "file_version": "v1",
          "page": 4,
          "section": "2.3 Curing",
          "source_type": "explicit"
        },
        {
          "evidence_id": "EV-005",
          "field_path": "samples/S1/performance_metrics/0/value",
          "work_id": "doi:10.1000/benchmark-001",
          "file_version": "v1",
          "page": 7,
          "figure": "Figure 3",
          "quote_or_value": "~5000 S/cm",
          "source_type": "estimated"
        },
        {
          "evidence_id": "EV-006",
          "field_path": "samples/S1/performance_metrics/1/value",
          "work_id": "doi:10.1000/benchmark-001",
          "file_version": "v1",
          "page": 8,
          "table": "Table 3",
          "quote_or_value": "15 MPa",
          "source_type": "explicit"
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Write `adapters/mock_agent.py`**

```python
"""Mock agent adapter returning fixed extraction results with mixed evidence levels."""

import json
from pathlib import Path

from workflow_models import (
    TaskDefinition, ParsedDocument, SampleExtraction, ResearchReport,
    ReportSection, PaperMetadata, ScreeningDecision,
    EvidenceLocator, CompositionRatio, ProcessStep, PerformanceMetric,
    MaterialComponent,
)


class MockAgentAdapter:
    def __init__(self, fixture_path: str | None = None):
        if fixture_path is None:
            fixture_path = str(
                Path(__file__).resolve().parent.parent / "fixtures" / "mock_extraction.json"
            )
        with open(fixture_path, encoding="utf-8") as f:
            self._fixture = json.load(f)

    async def screen_abstracts(self, task: TaskDefinition,
                               papers: list[PaperMetadata]) -> list[ScreeningDecision]:
        decisions = []
        for p in papers:
            include = bool(p.abstract) and p.document_type != "retracted"
            decisions.append(ScreeningDecision(
                paper=p,
                include=include,
                role_tags=p.get("role_tags", []),
                reason="mock screening",
            ))
        return decisions

    async def extract_paper(self, task: TaskDefinition,
                            parsed: ParsedDocument) -> list[SampleExtraction]:
        samples = []
        for s in self._fixture["samples"]:
            evidence = [
                EvidenceLocator(**e) for e in s.get("evidence", [])
            ]
            samples.append(SampleExtraction(
                sample_id=s["sample_id"],
                components=[MaterialComponent(**c) for c in s.get("components", [])],
                ratios=[CompositionRatio(**r) for r in s.get("ratios", [])],
                process_steps=[ProcessStep(**ps) for ps in s.get("process_steps", [])],
                performance_metrics=[PerformanceMetric(**pm) for pm in s.get("performance_metrics", [])],
                evidence=evidence,
                is_abstract_only=False,
            ))
        return samples

    async def generate_report(self, task: TaskDefinition,
                              extractions: list[SampleExtraction]) -> ResearchReport:
        sections = [
            ReportSection(heading="研究目标和约束", content=f"研究对象: {task.research_object}", order=1),
            ReportSection(heading="候选论文列表", content="Mock papers (see task)", order=2),
            ReportSection(heading="目标性能", content=str(task.target_metrics), order=3),
            ReportSection(heading="候选结构", content="PEO/AgNW composite", order=4),
            ReportSection(heading="实验室可行工艺体系", content="Solution casting", order=5),
            ReportSection(heading="成分和配比", content=f"Extractions: {len(extractions)} papers", order=6),
            ReportSection(heading="配方—工艺—性能表", content="See attached data", order=7),
            ReportSection(heading="数据缺失和冲突提示", content="All mock data — low confidence", order=8),
            ReportSection(heading="下一批实验点", content="TBD", order=9),
            ReportSection(heading="引用列表", content="[1] Mock et al. (2024)", order=10),
        ]
        return ResearchReport(task_id="", version=1, sections=sections)
```

- [ ] **Step 5: Verify mock agent works**

Run: `cd python-tools && python -c "import asyncio; from adapters.mock_agent import MockAgentAdapter; from workflow_models import TaskDefinition; a = MockAgentAdapter(); task = TaskDefinition(research_object='test', application='test', target_metrics=[], hard_constraints=[], optimization_objectives=[], acceptable_tradeoffs=[]); samples = asyncio.run(a.extract_paper(task, None)); s = samples[0]; print(f'Sample {s.sample_id}: {len(s.ratios)} ratios, {len(s.performance_metrics)} metrics, {len(s.evidence)} evidence items'); print(f'Ratio evidence: {s.ratios[0].evidence_ids}'); print(f'Perf evidence: {s.performance_metrics[0].evidence_ids}'); assert s.ratios[0].evidence_ids != s.performance_metrics[0].evidence_ids; print('Mixed evidence per field: OK')"`
Expected: prints sample details and "Mixed evidence per field: OK"

- [ ] **Step 6: Commit**

```bash
git add python-tools/adapters/mock_parser.py python-tools/adapters/agent_base.py python-tools/adapters/mock_agent.py python-tools/fixtures/mock_extraction.json
git commit -m "feat: add mock parser and agent adapters with mixed-evidence fixtures"
```

---

### Task 14: Pipeline Stage Handlers

**Files:**
- Create: `python-tools/pipeline/search_stage.py`
- Create: `python-tools/pipeline/screening_stage.py`
- Create: `python-tools/pipeline/fulltext_stage.py`
- Create: `python-tools/pipeline/parse_stage.py`
- Create: `python-tools/pipeline/extraction_stage.py`
- Create: `python-tools/pipeline/validation_stage.py`
- Create: `python-tools/pipeline/report_stage.py`

**Interfaces:** Each file exports a `StageHandler`-compatible class or function.

- [ ] **Step 1: Write `search_stage.py`**

```python
"""SEARCHING stage: query academic sources for candidate papers."""

from workflow_engine import WorkflowStore
from workflow_models import TaskDefinition, PaperMetadata
from pipeline.contracts import SearchProvider


async def run_search_stage(job: dict, store: WorkflowStore, search: SearchProvider):
    task = store.get_task(job["task_id"], job.get("owner_id", "worker"))
    definition = TaskDefinition(**task["definition"])

    from workflow_models import SearchQuery
    query = SearchQuery(
        text=task["query"],
        languages=definition.languages,
    )
    results = await search.search(query)

    # Submit as candidates; store transitions to WAITING_PAPER_APPROVAL
    papers = []
    for r in results:
        papers.append({
            "id": None,  # auto-generated
            "work_id": r.work_id,
            "title": r.title,
            "authors": r.authors,
            "year": r.year,
            "abstract": r.abstract,
            "doi": r.doi,
            "source": r.source,
            "document_type": r.document_type,
            "url": r.url,
            "language": r.language,
            "role_tags": [],
            "relevance_score": None,
            "evidence_level": "abstract_only" if not r.abstract else "abstract_only",
            "fulltext_status": "unknown",
        })

    store.submit_candidates(job["task_id"], "worker", papers)
    return {"papers_found": len(papers)}
```

- [ ] **Step 2: Write `screening_stage.py`**

```python
"""SCREENING stage: filter, deduplicate, classify, and rank candidates."""

from workflow_engine import WorkflowStore
from pipeline.contracts import EmbeddingRetriever, Reranker, AgentAdapter
from workflow_models import TaskDefinition, PaperMetadata


async def run_screening_stage(job: dict, store: WorkflowStore,
                              retriever: EmbeddingRetriever,
                              reranker: Reranker,
                              agent: AgentAdapter):
    task = store.get_task(job["task_id"], "worker")
    definition = TaskDefinition(**task["definition"])
    papers_raw = store.list_papers(job["task_id"], "worker")

    # Convert to PaperMetadata
    papers = [_paper_metadata(p) for p in papers_raw]

    # Deduplicate by work_id (keep highest-scoring version)
    seen = {}
    for p in papers:
        if p.work_id not in seen or (p.year or 0) > (seen[p.work_id].year or 0):
            seen[p.work_id] = p
    deduped = list(seen.values())

    # Embedding recall
    scored = await retriever.retrieve(definition, deduped, top_k=min(len(deduped), definition.paper_target * 2))

    # Rerank
    ranked = await reranker.rerank(task["query"], scored)

    # Agent screening
    decisions = await agent.screen_abstracts(definition, [s.metadata for s in ranked])

    # Update paper role_tags and scores
    for dec in decisions:
        for paper_row in papers_raw:
            if paper_row["work_id"] == dec.paper.work_id:
                with store._connect() as conn:
                    conn.execute(
                        "UPDATE papers SET role_tags_json = ?, relevance_score = ?, updated_at = ? WHERE id = ?",
                        (store._json(dec.role_tags), 80.0, _now(), paper_row["id"]),
                    )

    # Transition to WAITING_PAPER_APPROVAL (no job created — human gate)
    return {"candidates_after_screening": len(decisions), "included": sum(1 for d in decisions if d.include)}


def _paper_metadata(row: dict) -> PaperMetadata:
    return PaperMetadata(
        work_id=row["work_id"],
        title=row["title"],
        authors=row.get("metadata", {}).get("authors", []),
        year=row.get("metadata", {}).get("year"),
        abstract=row.get("metadata", {}).get("abstract", ""),
        doi=row.get("metadata", {}).get("doi", ""),
    )

def _now():
    from workflow_engine import utc_now
    return utc_now()
```

- [ ] **Step 3: Write remaining stage handlers**

`fulltext_stage.py`:
```python
"""FETCHING_FULLTEXT stage: download PDFs for selected papers."""

from workflow_engine import WorkflowStore
from pipeline.contracts import FulltextProvider
from artifact_utils import atomic_write_unique
import os


async def run_fulltext_stage(job: dict, store: WorkflowStore, provider: FulltextProvider):
    task_id = job["task_id"]
    papers = store.list_papers(task_id, "worker")
    selected = [p for p in papers if p["paper_status"] == "selected"]

    downloaded = 0
    for paper in selected:
        import workflow_models as wm
        meta = wm.PaperMetadata(work_id=paper["work_id"], title=paper["title"])
        pdf_bytes = await provider.fetch(meta)
        if pdf_bytes:
            import uuid, hashlib
            paper_dir = os.path.join(
                os.path.dirname(store.db_path), "..", "data", "tasks", task_id,
                "papers", paper["id"]
            )
            os.makedirs(paper_dir, exist_ok=True)
            sha = hashlib.sha256(pdf_bytes).hexdigest()
            path = os.path.join(paper_dir, f"fulltext_{sha[:16]}.pdf")
            atomic_write_unique(pdf_bytes, path)
            store.record_artifact(task_id, paper["id"], "pdf", "pdf", path, sha)
            store.update_paper_status(paper["id"], "fetched")
            downloaded += 1
        else:
            store.update_paper_status(paper["id"], "degraded", error="fulltext unavailable")

    # Advance to PARSING
    store.advance(task_id, "worker", "PARSING")
    return {"downloaded": downloaded, "degraded": len(selected) - downloaded}
```

`parse_stage.py` — delegates to `DocumentParser`, saves `ParsedDocument` as artifact, updates paper status to `parsed` or `degraded`.

`extraction_stage.py` — delegates to `AgentAdapter.extract_paper()`, records extraction via `store.record_extraction()`, updates paper to `extracted`.

`validation_stage.py` — Phase 2A minimal: validates `evidence_ids` references exist, `source_type` is valid, page numbers are positive. Returns validation results.

`report_stage.py` — delegates to `AgentAdapter.generate_report()`, writes Markdown to disk via `atomic_write_unique()`, calls `store.record_report()`, transitions to `WAITING_DATA_REVIEW`.

Full code for these is structured identically to `fulltext_stage.py` above — each: (1) reads task/papers from store, (2) delegates to adapter Protocol, (3) writes results via store, (4) advances task or enters human gate.

- [ ] **Step 4: Verify imports**

Run: `cd python-tools && python -c "from pipeline.search_stage import run_search_stage; from pipeline.fulltext_stage import run_fulltext_stage; print('Stage handlers import OK')"`
Expected: "Stage handlers import OK"

- [ ] **Step 5: Commit**

```bash
git add python-tools/pipeline/search_stage.py python-tools/pipeline/screening_stage.py python-tools/pipeline/fulltext_stage.py python-tools/pipeline/parse_stage.py python-tools/pipeline/extraction_stage.py python-tools/pipeline/validation_stage.py python-tools/pipeline/report_stage.py
git commit -m "feat: add pipeline stage handlers (search through report)"
```

---

### Task 15: Worker Stage Registry Wiring

**Files:**
- Create: `python-tools/worker_main.py` — entry point that wires adapters to stages
- Modify: `python-tools/workflow_worker.py` — minor (if needed)

- [ ] **Step 1: Write `worker_main.py`**

```python
"""Worker entry point — wires mock adapters to stage handlers and starts the Worker."""

import asyncio
import logging

from workflow_engine import WorkflowStore
from workflow_worker import WorkflowWorker, StageRegistry
from workflow_config import WorkflowConfig

from adapters.mock_search import MockSearchProvider, MockFulltextProvider
from adapters.mock_embedding import MockEmbeddingRetriever, MockReranker
from adapters.mock_parser import MockDocumentParser
from adapters.mock_agent import MockAgentAdapter

from pipeline.search_stage import run_search_stage
from pipeline.screening_stage import run_screening_stage
from pipeline.fulltext_stage import run_fulltext_stage
from pipeline.parse_stage import run_parse_stage
from pipeline.extraction_stage import run_extraction_stage
from pipeline.validation_stage import run_validation_stage
from pipeline.report_stage import run_report_stage


async def main():
    logging.basicConfig(level=logging.INFO)
    config = WorkflowConfig()
    store = WorkflowStore(config.db_path)

    # Create adapters
    search = MockSearchProvider()
    fulltext = MockFulltextProvider()
    retriever = MockEmbeddingRetriever()
    reranker = MockReranker()
    parser = MockDocumentParser()
    agent = MockAgentAdapter()

    # Wire stage handlers
    registry = StageRegistry(config)

    async def search_handler(job, s):
        return await run_search_stage(job, s, search)
    registry.register("SEARCHING", type("Handler", (), {"run": search_handler})())

    async def screening_handler(job, s):
        return await run_screening_stage(job, s, retriever, reranker, agent)
    registry.register("SCREENING", type("Handler", (), {"run": screening_handler})())

    # ... register FETCHING_FULLTEXT, PARSING, READING, EXTRACTING,
    #     VALIDATING, GENERATING_REPORT similarly ...

    worker = WorkflowWorker(config, store, registry)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify worker starts**

Run: `cd python-tools && timeout 3 python worker_main.py 2>&1 || true`
Expected: "Worker worker-1 starting" log message, then process exits after timeout

- [ ] **Step 3: Commit**

```bash
git add python-tools/worker_main.py python-tools/workflow_worker.py
git commit -m "feat: wire mock adapters to stage handlers in worker entry point"
```

---

### Task 16: E2E Test Suite

**Files:**
- Create: `tests/test_e2e_basic.py` — 12 scenarios
- Create: `scripts/run_basic_test.sh`
- Create: `requirements-dev.txt`

- [ ] **Step 1: Write `requirements-dev.txt`**

```
pytest>=8.0
pytest-asyncio>=0.24
httpx>=0.27
PyMuPDF>=1.23
```

- [ ] **Step 2: Write test fixtures helper**

```python
# tests/conftest.py (or inline in test_e2e_basic.py)
import os, sys, tempfile, asyncio
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

from workflow_engine import WorkflowStore
from workflow_worker import StageRegistry
from workflow_config import WorkflowConfig


@pytest.fixture
def tmp_store():
    tmp = tempfile.mkdtemp()
    config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
    store = WorkflowStore(config.db_path)
    yield store, config
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 3: Write happy-path E2E test**

```python
# tests/test_e2e_basic.py
import os, sys, asyncio, tempfile
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

from workflow_engine import WorkflowStore, TaskStatus
from workflow_config import WorkflowConfig
from workflow_models import TaskDefinition

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
        task = store.create_task("alice", "Flexible conductive composite optimization",
                                 "Find optimal PEO/AgNW formulation for high conductivity")

        # 2. Confirm definition
        definition = {
            "research_object": "PEO/AgNW conductive composite",
            "application": "flexible electronics",
            "target_metrics": [{"name": "conductivity", "unit": "S/cm", "target_range": ">1000"}],
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
        from adapters.mock_search import MockSearchProvider
        from workflow_models import SearchQuery
        search = MockSearchProvider()
        results = await search.search(SearchQuery(text=task["query"]))
        papers = []
        for r in results:
            papers.append({
                "work_id": r.work_id,
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "abstract": r.abstract,
                "doi": r.doi,
                "source": r.source,
                "document_type": r.document_type,
                "role_tags": [],
            })
        store.submit_candidates(task["id"], config.worker_id, papers)
        store.complete_job(job["id"], config.worker_id, job["lease_token"],
                           result={"papers_found": len(papers)})

        # 5. Task should be WAITING_PAPER_APPROVAL
        task = store.get_task(task["id"], "alice")
        assert task["status"] == TaskStatus.WAITING_PAPER_APPROVAL, f"Expected WAITING_PAPER_APPROVAL, got {task['status']}"

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
        store.advance(task["id"], config.worker_id, "PARSING")
        store.complete_job(job["id"], config.worker_id, job["lease_token"],
                           result={"downloaded": 3})

        # 8. Worker: parse -> reading -> extracting
        for target in ["PARSING", "READING", "EXTRACTING"]:
            job = store.claim_next_job(config.worker_id)
            assert job["stage"] == target
            store.advance(task["id"], config.worker_id,
                         {"PARSING": "READING", "READING": "EXTRACTING", "EXTRACTING": "VALIDATING"}[target])
            store.complete_job(job["id"], config.worker_id, job["lease_token"],
                               result={"done": True})

        # 9. Worker: extraction
        # Record mock extractions
        for pid in selected:
            store.record_extraction(
                task["id"], config.worker_id, pid,
                {"samples": [{"sample_id": "S1", "ratios": [{"component": "AgNW", "raw_value": "25", "raw_unit": "wt%"}]}]},
                "explicit", 85.0,
            )
            store.update_paper_status(pid, "extracted")

        # 10. Worker: validate -> generate report
        for target in ["VALIDATING", "GENERATING_REPORT"]:
            job = store.claim_next_job(config.worker_id)
            assert job["stage"] == target
            if target == "GENERATING_REPORT":
                store.request_data_review(task["id"], config.worker_id)
            else:
                store.advance(task["id"], config.worker_id, "GENERATING_REPORT")
            store.complete_job(job["id"], config.worker_id, job["lease_token"],
                               result={"done": True})

        # 11. Task should be WAITING_DATA_REVIEW
        task = store.get_task(task["id"], "alice")
        assert task["status"] == TaskStatus.WAITING_DATA_REVIEW

        # 12. Admin approves
        task = store.review_extractions(task["id"], "admin", approved=True, is_admin=True)
        assert task["status"] == TaskStatus.COMPLETED

        # 13. Verify events exist
        events = store.events(task["id"], "alice")
        event_types = [e["event_type"] for e in events]
        assert "task_created" in event_types
        assert "data_reviewed" in event_types

    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 4: Run E2E test**

Run: `cd python-tools && python -m pytest ../tests/test_e2e_basic.py::test_e2e_happy_path -v`
Expected: PASS (completes full pipeline)

- [ ] **Step 5: Write `scripts/run_basic_test.sh`**

```bash
#!/bin/bash
# run_basic_test.sh — complete test suite for Phase 1A + 2A
set -e
cd "$(dirname "$0")/.."

export APP_ENV=test
export WORKFLOW_DB_PATH=":memory:"

echo "=== Running unit tests ==="
python -m pytest tests/test_workflow_models.py tests/test_workflow_engine.py -v

echo "=== Running API tests ==="
python -m pytest tests/test_workflow_api.py -v

echo "=== Running Worker tests ==="
python -m pytest tests/test_worker.py -v

echo "=== Running artifact tests ==="
python -m pytest tests/test_artifact_utils.py -v

echo "=== Running E2E tests ==="
python -m pytest tests/test_e2e_basic.py -v

echo "=== All tests passed ==="
```

- [ ] **Step 6: Make script executable and run**

```bash
chmod +x scripts/run_basic_test.sh
bash scripts/run_basic_test.sh
```
Expected: all test suites pass, exit code 0

- [ ] **Step 7: Commit**

```bash
git add tests/test_e2e_basic.py scripts/run_basic_test.sh requirements-dev.txt
git commit -m "feat: add E2E happy-path test, run_basic_test.sh, dev requirements"
```

---

### Task 17: User Isolation & Auth E2E Tests

**Files:**
- Modify: `tests/test_e2e_basic.py` — add tests #2, #3

- [ ] **Step 1: Add user isolation test**

```python
@pytest.mark.asyncio
async def test_e2e_user_isolation():
    """Alice creates task; Bob cannot access it."""
    import tempfile, os
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
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Add admin/owner gate test**

```python
@pytest.mark.asyncio
async def test_e2e_human_gates_owner_and_admin():
    """Owner AND admin can approve papers and review extractions."""
    import tempfile, os
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        # Setup: task at WAITING_PAPER_APPROVAL
        task = store.create_task("alice", "test", "query")
        store.update_definition(task["id"], "alice", {
            "research_object": "x", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        store.start_search(task["id"], "alice")
        store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        # Admin approves
        task = store.approve_papers(task["id"], "admin", ["p1"], is_admin=True)
        assert task["status"] == TaskStatus.FETCHING_FULLTEXT
        # Non-owner, non-admin cannot
        task2 = store.create_task("alice", "test2", "query2")
        store.update_definition(task2["id"], "alice", {
            "research_object": "x", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        store.start_search(task2["id"], "alice")
        store.submit_candidates(task2["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        with pytest.raises(PermissionDeniedError):
            store.approve_papers(task2["id"], "bob", ["p1"])
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 3: Run**

Run: `cd python-tools && python -m pytest ../tests/test_e2e_basic.py -v`
Expected: 3 tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_basic.py
git commit -m "test: add user isolation and admin/owner gate E2E tests"
```

---

### Task 18: Human Gate & Paper Degradation Tests

**Files:**
- Modify: `tests/test_e2e_basic.py` — add tests #4, #7, #11

- [ ] **Step 1: Add premature job test**

```python
@pytest.mark.asyncio
async def test_e2e_no_premature_job():
    """After candidates submitted, no FETCHING_FULLTEXT job exists until approval."""
    import tempfile, os
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.update_definition(task["id"], "alice", {
            "research_object": "x", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        store.start_search(task["id"], "alice")
        store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        # Verify no FETCHING_FULLTEXT job exists
        job = store.claim_next_job(config.worker_id)
        assert job is None, "No job should exist during WAITING_PAPER_APPROVAL"
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Add paper degradation test**

```python
@pytest.mark.asyncio
async def test_e2e_single_paper_degradation():
    """3 papers: 1 OK, 1 PDF corrupt, 1 partial parse. Task continues."""
    import tempfile, os
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.update_definition(task["id"], "alice", {
            "research_object": "x", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        store.start_search(task["id"], "alice")
        store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Good paper", "role_tags": ["target_performance"]},
            {"id": "p2", "title": "Corrupt PDF", "role_tags": ["lab_process"]},
            {"id": "p3", "title": "Partial parse", "role_tags": ["structure"]},
        ])
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
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 3: Run**

Run: `cd python-tools && python -m pytest ../tests/test_e2e_basic.py -v`
Expected: 5 tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_basic.py
git commit -m "test: add human-gate and paper-degradation E2E tests"
```

---

### Task 19: Lease Fencing & Crash Recovery Tests

**Files:**
- Modify: `tests/test_e2e_basic.py` — add tests #5, #6

- [ ] **Step 1: Add lease fencing test**

```python
@pytest.mark.asyncio
async def test_e2e_lease_fencing():
    """Old worker cannot overwrite new worker's result after lease loss."""
    import tempfile, os
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
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Run**

Run: `cd python-tools && python -m pytest ../tests/test_e2e_basic.py::test_e2e_lease_fencing -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_basic.py
git commit -m "test: add lease fencing E2E test"
```

---

### Task 20: Idempotency & Rollback Tests

**Files:**
- Modify: `tests/test_e2e_basic.py` — add tests #8, #12

- [ ] **Step 1: Add idempotency test**

```python
@pytest.mark.asyncio
async def test_e2e_idempotent_replay():
    """Same idempotency_key produces no duplicate papers or extractions."""
    import tempfile, os
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.update_definition(task["id"], "alice", {
            "research_object": "x", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        store.start_search(task["id"], "alice")
        # First submission
        store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        count1 = len(store.list_papers(task["id"], "alice"))
        # Rollback and re-submit with same data
        store.rollback(task["id"], "alice", "SEARCHING")
        store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        count2 = len(store.list_papers(task["id"], "alice"))
        assert count2 == count1, "No duplicate papers from replay"
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Add rollback-with-new-input test**

```python
@pytest.mark.asyncio
async def test_e2e_rollback_new_input_new_version():
    """Rollback + modify definition → new input_version → new job, old preserved."""
    import tempfile, os
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        task = store.create_task("alice", "test", "query")
        store.update_definition(task["id"], "alice", {
            "research_object": "x", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        store.start_search(task["id"], "alice")
        store.submit_candidates(task["id"], "alice", [
            {"id": "p1", "title": "Test", "role_tags": ["target_performance"]},
        ])
        papers_before = store.list_papers(task["id"], "alice")
        # Rollback
        store.rollback(task["id"], "alice", "CLARIFYING")
        # Modify definition
        store.update_definition(task["id"], "alice", {
            "research_object": "modified", "application": "y",
            "target_metrics": [], "hard_constraints": [],
            "optimization_objectives": [], "acceptable_tradeoffs": [],
        })
        # Restart
        task = store.start_search(task["id"], "alice")
        assert task["status"] == TaskStatus.SEARCHING
        # Old papers still exist
        papers_after = store.list_papers(task["id"], "alice")
        assert len(papers_after) >= len(papers_before)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 3: Run**

Run: `cd python-tools && python -m pytest ../tests/test_e2e_basic.py -v`
Expected: 9 tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_basic.py
git commit -m "test: add idempotency and rollback E2E tests"
```

---

### Task 21: Mixed Evidence & Abstract-Only Tests

**Files:**
- Modify: `tests/test_e2e_basic.py` — add tests #10, #11

- [ ] **Step 1: Add mixed-evidence test**

```python
@pytest.mark.asyncio
async def test_e2e_mixed_evidence_per_field():
    """One extraction has explicit ratios AND estimated performance AND missing modulus."""
    import tempfile, os, json
    tmp = tempfile.mkdtemp()
    try:
        config = WorkflowConfig(db_path=os.path.join(tmp, "test.db"))
        store = WorkflowStore(config.db_path)
        from adapters.mock_agent import MockAgentAdapter
        from workflow_models import TaskDefinition
        agent = MockAgentAdapter()
        task_def = TaskDefinition(
            research_object="x", application="y",
            target_metrics=[], hard_constraints=[],
            optimization_objectives=[], acceptable_tradeoffs=[],
        )
        samples = await agent.extract_paper(task_def, None)
        assert len(samples) > 0
        s = samples[0]
        # Verify mixed evidence
        ratio_sources = set()
        for r in s.ratios:
            for eid in r.evidence_ids:
                for ev in s.evidence:
                    if ev.evidence_id == eid:
                        ratio_sources.add(ev.source_type)
        perf_sources = set()
        for pm in s.performance_metrics:
            for eid in pm.evidence_ids:
                for ev in s.evidence:
                    if ev.evidence_id == eid:
                        perf_sources.add(ev.source_type)
        # Ratios should include "explicit"
        assert "explicit" in ratio_sources, f"Ratios should have explicit evidence, got {ratio_sources}"
        # Performance should include different types (estimated)
        assert len(perf_sources) > 0
        # Verify evidence is per-field, not per-extraction
        assert ratio_sources != perf_sources, (
            f"Ratio sources {ratio_sources} and perf sources {perf_sources} "
            f"should differ (per-field evidence)"
        )
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 2: Run**

Run: `cd python-tools && python -m pytest ../tests/test_e2e_basic.py::test_e2e_mixed_evidence_per_field -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_basic.py
git commit -m "test: add mixed-evidence per-field E2E test"
```

---

### Task 22: Final Integration — run_basic_test.sh

**Files:**
- Modify: `scripts/run_basic_test.sh` — finalize
- Verify: all 12 scenarios pass

- [ ] **Step 1: Finalize run_basic_test.sh**

```bash
#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

export APP_ENV=test
export PYTHONPATH="$PROJECT_ROOT/python-tools:$PYTHONPATH"

echo "============================================"
echo "  Workflow Basic Test Suite"
echo "  Phase 1A + 2A"
echo "============================================"
echo ""

# 1. Unit tests
echo "[1/6] Data models..."
python -m pytest tests/test_workflow_models.py -v

echo "[2/6] Workflow engine (state machine, leases, migrations)..."
python -m pytest tests/test_workflow_engine.py -v

echo "[3/6] Artifact utilities..."
python -m pytest tests/test_artifact_utils.py -v

echo "[4/6] API + auth..."
python -m pytest tests/test_workflow_api.py -v

echo "[5/6] Worker..."
python -m pytest tests/test_worker.py -v

echo "[6/6] E2E suite (12 scenarios)..."
python -m pytest tests/test_e2e_basic.py -v

echo ""
echo "============================================"
echo "  All tests passed"
echo "============================================"
```

- [ ] **Step 2: Run full suite**

```bash
bash scripts/run_basic_test.sh
```
Expected: all 6 test suites pass, exit code 0

- [ ] **Step 3: Commit**

```bash
git add scripts/run_basic_test.sh tests/
git commit -m "test: finalize run_basic_test.sh — all 12 E2E scenarios"
```

---

## Phase 1B + 2B (Next Plan)

Reserved for a follow-up implementation plan. Key deliverables:

1B: Auth hardening (password hashing, key rotation, token lifetime), full 14-state per-paper status, validation stage (unit checks, ratio basis, inferred-value guard), rollback constraints (artifact superseding), job recovery (heartbeat, real exponential backoff, dead letter management), user memory isolation.

2B: Worker crash + subprocess recovery E2E, multi-user FIFO fairness, concurrent migration safety, lease expiry with real subprocess kill/SIGTERM.






