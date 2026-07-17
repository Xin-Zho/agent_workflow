# Workflow Foundation + Mock E2E — Design Spec

**Date:** 2026-07-17
**Status:** draft (revised after review)
**Scope:** Phase 1A → 2A → 1B → 2B (4 milestones)

## 1. Problem Statement

The current workflow system has a state-machine skeleton but cannot run a real end-to-end test:

- No Worker process consumes queued jobs — transitions require manual `/advance` calls.
- No structured data models — arbitrary `dict` payloads are stored as JSON.
- No job lease mechanism — a crashed Worker leaves jobs permanently stuck in `running`.
- Auth is broken — `/api/auth/token` accepts arbitrary `user_id` with no credential check.
- Single global Pi event queue — multi-user message mixing.
- Hardcoded `USER_ID=1` — all users share the same memory.
- No artifact persistence — reports and intermediate results are lost on restart.
- No per-paper status — one paper failure is indistinguishable from task failure.
- No field-level evidence tracking — an extraction marked `explicit` cannot express that the ratio is explicit but the performance value is estimated.

## 2. Goals

1. A **Worker process** that consumes FIFO jobs and advances tasks through the full pipeline.
2. **Mock adapters** for all external capabilities (search, embedding, agent, PDF parsing) so the pipeline can run without IEEE/WoS/35B model access.
3. A **suite of E2E tests** covering happy path, failure, recovery, and multi-user isolation.
4. **Data integrity**: idempotent stages, job leases with renewal and fencing, per-paper status, artifact persistence, field-level evidence.
5. **Basic auth**: users cannot impersonate each other.
6. **Human gates**: `WAITING_PAPER_APPROVAL` and `WAITING_DATA_REVIEW` are never auto-advanced — enforced at both the Worker and database layer.

Non-goals for this spec: production auth (OAuth/LDAP), real IEEE/WoS integration, real 35B model calls, advanced PDF OCR/curve digitization, full admin dashboard, CORS hardening, Worker-to-API internal RPC.

## 3. Milestones

### Phase 1A — Minimal Foundation

| # | Deliverable | Files |
|---|------------|-------|
| 1 | Structured data models with field-level evidence (Pydantic) | `workflow_models.py` (new) |
| 2 | Database migration system with checksums | `migrations/` (new); `workflow_engine.py` (modify) |
| 3 | Job lease mechanism with lease_token, renewal, and fencing | `workflow_engine.py` (modify) |
| 4 | Worker framework (claim → renew → complete/fail/retry) | `workflow_worker.py` (new) |
| 5 | Pipeline contracts (Protocol classes) | `pipeline/contracts.py` (new) |
| 6 | Artifact storage with atomic writes | `workflow_engine.py` (modify) |
| 7 | State transition tables (Task, Job, Paper) with permissions | `workflow_engine.py` (modify) |
| 8 | Minimal per-paper status (7 states) | `workflow_engine.py` (modify) |
| 9 | Human-gate constraints (DB-level prevention of jobs for wait states) | `workflow_engine.py` (modify) |
| 10 | Input validation + idempotency_key UNIQUE constraint | `workflow_engine.py`, `workflow_api.py` (modify) |

### Phase 2A — Mock Happy-Path E2E

| # | Deliverable | Files |
|---|------------|-------|
| 11 | Mock SearchProvider | `adapters/mock_search.py` (new) |
| 12 | Mock EmbeddingRetriever | `adapters/mock_embedding.py` (new) |
| 13 | Mock Reranker | `adapters/mock_embedding.py` |
| 14 | Mock FulltextProvider | `adapters/mock_search.py` |
| 15 | Mock DocumentParser | `adapters/mock_parser.py` (new) |
| 16 | Mock AgentAdapter (mixed evidence per field) | `adapters/mock_agent.py` (new) |
| 17 | Markdown ReportGenerator | `pipeline/report_stage.py` (new) |
| 18 | Stage handlers (search, screening, parse, extract, validate, report) | `pipeline/*.py` (new) |
| 19 | E2E test suite (12 scenarios) | `tests/test_e2e_basic.py` (new) |
| 20 | Test fixtures (papers.json, sample PDF, mock extraction) | `fixtures/` (new) |
| 21 | `scripts/run_basic_test.sh` | `scripts/run_basic_test.sh` (new) |

### Phase 1B — Foundation Hardening

| # | Deliverable |
|---|------------|
| 22 | Auth hardening (credentials, JWT role, ownership checks) |
| 23 | Full per-paper status (14 states) with error tracking |
| 24 | Validation stage (unit checks, ratio basis, page/figure locator, inferred-value guard) |
| 25 | Rollback constraints (direction check, artifact superseding, human-gate re-entry) |
| 26 | Job recovery (heartbeat, lease expiry, exponential backoff, max retry, dead letter) |
| 27 | User memory isolation (per-user MemoryManager, per-task context, reviewed-only sharing) |

### Phase 2B — Resilience & Multi-User E2E

| # | Test Scenario |
|---|--------------|
| 28 | Single paper failure — others continue, report marks evidence level |
| 29 | Worker crash mid-stage — lease expires, old Worker fenced, new Worker resumes |
| 30 | Two users — isolation, FIFO fairness, no cross-access |
| 31 | Human gates — Worker waits, owner reviews, admin reviews, non-owner blocked |
| 32 | Idempotent replay — same key produces no duplicates |
| 33 | Concurrent API+Worker startup — migration runs exactly once |

## 4. Architecture

```
┌─ web_api_server.py ────────────────────────────────────────┐
│  FastAPI app                                                 │
│  ├─ /api/auth/*          (JWT with role)                     │
│  ├─ /api/research/*       (workflow_api.py)                  │
│  └─ /ws, /api/chat/*      (Pi RPC, per-session queues)      │
└────────────┬────────────────────────────────────────────────┘
             │ coordinates state + enqueues jobs via DB
             ▼
┌─ workflow_engine.py (WorkflowStore) ────────────────────────┐
│  SQLite database (sole coordination point)                   │
│  ├─ schema_migrations (version, name, checksum, applied_at)  │
│  ├─ tasks (with revision for optimistic locking)             │
│  ├─ papers (with paper_status, error_message)                │
│  ├─ extractions (versioned, per-paper)                       │
│  ├─ jobs (with worker_id, lease_token, lease_expires_at,     │
│  │        idempotency_key UNIQUE, max_attempts)              │
│  ├─ job_attempts (per-attempt audit trail)                   │
│  ├─ artifacts (path, sha256, type — metadata only)           │
│  └─ reports (versioned markdown paths)                       │
└────────┬───────────────────────────────────────────────────┘
         │ claim / complete / renew-lease
         ▼
┌─ workflow_worker.py ───────────────────────────────────────┐
│  Separate process. Direct DB access (no HTTP to API).        │
│                                                              │
│  while True:                                                 │
│      job = store.claim_next_job(worker_id)                   │
│      if not job: sleep(poll_interval); continue              │
│      if long_running: periodically store.renew_lease(...)    │
│      try:                                                    │
│          handler = registry.get(job.stage)                   │
│          result = handler.run(job, store)                    │
│          store.complete_job(job.id, worker_id, lease_token)  │
│      except RetryableError:                                  │
│          store.retry_job(job.id, worker_id, lease_token)     │
│      except FatalError:                                      │
│          store.fail_job(job.id, worker_id, lease_token)      │
└──────────────────┬──────────────────────────────────────────┘
                   │
                   ▼
          ┌─ pipeline/ ──────────────┐
          │  contracts.py (Protocols)│
          │  search_stage.py          │
          │  screening_stage.py       │
          │  parse_stage.py           │
          │  extraction_stage.py      │
          │  validation_stage.py      │
          │  report_stage.py          │
          └─────────┬─────────────────┘
                    │
                    ▼
          ┌─ adapters/ ──────────────┐
          │  mock_search.py           │
          │  mock_embedding.py        │
          │  mock_parser.py           │
          │  mock_agent.py            │
          │  (future: pi_agent, ...)  │
          └───────────────────────────┘
```

**API ↔ Worker boundary:** Coordinated solely through the SQLite database. API enqueues work by changing task status (which creates a job row). Worker claims jobs, executes pipeline stages, and writes results back. Large files (PDFs, parsed output, reports) are stored on the shared filesystem under `data/tasks/<task_id>/`; the database stores only paths, hashes, and metadata. Worker does NOT call the API over HTTP — it reads and writes the same SQLite database directly.

**Pipeline ↔ Adapter boundary:** Stage handlers depend on Protocol interfaces, never concrete adapters. Adapter selection is configuration-driven.

## 5. Data Models

### 5.1 Field-Level Evidence

This is the foundational design decision. `source_type` is per-evidence, NOT per-extraction. One paper's extraction can simultaneously contain:
- A ratio copied verbatim from Table 2 (explicit)
- A performance value read from a graph (estimated)
- A curing time inferred from context (inferred)
- A missing modulus value (missing)

Every data-carrying field (ratio, process parameter, performance metric, test condition) references its evidence via `evidence_ids`.

```python
class EvidenceLocator(BaseModel):
    """Locates and characterizes the source of a single data point."""
    evidence_id: str  # unique within the extraction, e.g. "EV-001"
    field_path: str   # JSONPath-like: "samples/S1/ratios/2/raw_value"
    work_id: str
    file_version: str
    page: int
    section: str | None = None
    figure: str | None = None
    table: str | None = None
    quote_or_value: str | None = None  # verbatim text from source
    source_type: Literal["explicit", "derived", "estimated", "inferred", "missing"]
```

### 5.2 Core Models

```python
class TaskDefinition(BaseModel):
    research_object: str
    application: str
    target_metrics: list[Metric]
    hard_constraints: list[str]
    optimization_objectives: list[str]
    acceptable_tradeoffs: list[str]
    paper_target: int = Field(ge=5, le=200)
    languages: list[str] = ["zh", "en"]
    temporary_lab_constraints: list[str] = []

class Metric(BaseModel):
    name: str
    unit: str | None = None
    target_range: str | None = None  # e.g. ">200 MPa"

class MaterialComponent(BaseModel):
    name: str
    role: str | None = None
    supplier: str | None = None

class RatioBasis(StrEnum):
    """What the ratio is relative to — required for comparability checks."""
    MASS_FRACTION = "mass_fraction"         # wt%
    VOLUME_FRACTION = "volume_fraction"     # vol%
    MOLE_FRACTION = "mole_fraction"         # mol%
    MASS_PARTS = "mass_parts"               # phr (per hundred resin/rubber)
    RELATIVE_TO_MATRIX = "relative_to_matrix"
    RELATIVE_TO_TOTAL = "relative_to_total"
    RELATIVE_TO_PRECURSOR = "relative_to_precursor"
    UNSPECIFIED = "unspecified"

class CompositionRatio(BaseModel):
    component: str
    raw_value: str
    raw_unit: str
    ratio_basis: RatioBasis = RatioBasis.UNSPECIFIED
    normalized_value: float | None = None
    normalized_unit: str | None = None
    evidence_ids: list[str] = []  # references EvidenceLocator.evidence_id

class ProcessStep(BaseModel):
    step_number: int
    description: str
    parameters: dict[str, Any]
    equipment: str | None = None
    evidence_ids: list[str] = []

class TestCondition(BaseModel):
    property: str
    method: str | None = None
    standard: str | None = None
    parameters: dict[str, Any]
    evidence_ids: list[str] = []

class PerformanceMetric(BaseModel):
    property: str
    value: str
    unit: str
    test_condition: str | None = None
    evidence_ids: list[str] = []

class SampleExtraction(BaseModel):
    sample_id: str
    components: list[MaterialComponent]
    ratios: list[CompositionRatio]
    process_steps: list[ProcessStep]
    test_conditions: list[TestCondition]
    performance_metrics: list[PerformanceMetric]
    evidence: list[EvidenceLocator]  # all evidence items referenced by evidence_ids above
    is_abstract_only: bool = False  # True → cannot enter quantitative comparison

class ParsedBlock(BaseModel):
    """A stable text block within a parsed page."""
    block_id: str          # unique within the document, e.g. "p3-b12"
    page_number: int
    block_type: str        # "paragraph", "table", "figure_caption", "section_heading"
    text: str
    bbox: tuple[float, float, float, float] | None = None  # for future visual anchoring

class ParsedPage(BaseModel):
    page_number: int
    blocks: list[ParsedBlock]
    captions: list[str]
    tables: list[dict[str, Any]]

class ParsedDocument(BaseModel):
    work_id: str
    file_version: str
    pages: list[ParsedPage]
    metadata: dict[str, Any]

class ResearchReport(BaseModel):
    task_id: str
    version: int
    sections: list[ReportSection]
    format: str = "markdown"

class ReportSection(BaseModel):
    heading: str
    content: str
    order: int
```

### 5.3 Work and Version Model

Papers are identified by a stable `work_id` (e.g. `doi:10.1000/example`). Versions (preprint, journal, corrigendum) are separate entities under the same work:

```python
class WorkVersion(StrEnum):
    PREPRINT = "preprint"
    JOURNAL = "journal"
    CORRIGENDUM = "corrigendum"
    UNKNOWN = "unknown"
```

The `papers` table's `version_id` field uses this enum, not free text.

### 5.4 Paper Status (7 states for Phase 1A)

```python
class PaperStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    FETCHED = "fetched"
    PARSED = "parsed"
    EXTRACTED = "extracted"
    DEGRADED = "degraded"
    FAILED = "failed"
```

Single paper failure (e.g. PDF corrupt) → `DEGRADED` or `FAILED`. Batch stages must NOT retry the entire batch because one paper failed.

### 5.5 Job States

```python
class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"
```

## 6. State Transition Tables

### 6.1 Task State Transitions

```
Current State            → Target State             Trigger                 Creates Job?  Allowed By
─────────────────────────────────────────────────────────────────────────────────────────────────────
CLARIFYING               → SEARCHING                start_search            yes           owner
CLARIFYING               → DRAFT                    update_definition       no            owner
DRAFT                    → SEARCHING                start_search            yes           owner
SEARCHING                → SCREENING                Worker stage complete   yes           Worker
SCREENING                → WAITING_PAPER_APPROVAL   Worker stage complete   no            Worker
WAITING_PAPER_APPROVAL   → FETCHING_FULLTEXT        approve_papers          yes           owner
FETCHING_FULLTEXT        → PARSING                  Worker stage complete   yes           Worker
PARSING                  → READING                  Worker stage complete   yes           Worker
READING                  → EXTRACTING               Worker stage complete   yes           Worker
EXTRACTING               → VALIDATING               Worker stage complete   yes           Worker
VALIDATING               → GENERATING_REPORT        Worker stage complete   yes           Worker
GENERATING_REPORT        → WAITING_DATA_REVIEW      Worker stage complete   no            Worker
WAITING_DATA_REVIEW      → COMPLETED                review_extractions      no            owner/admin
WAITING_DATA_REVIEW      → EXTRACTING               review_extractions      yes           owner/admin
                                                                             (rejected)
Any (except COMPLETED)   → PAUSED                   pause                   no            owner
PAUSED                   → {previous_status}        resume                  conditional* owner
Any (except COMPLETED)   → {earlier_stage}          rollback                conditional* owner
Any                      → FAILED                   fatal error             no            Worker/system
Any                      → DEGRADED                 partial failure         no            Worker/system
```

*resume creates a job only if the previous_status is an automated pipeline stage (not a human-wait state).
*rollback creates a job only if the target is an automated pipeline stage.
*rollback targets must be earlier in the pipeline than current status.

### 6.2 Human-Gate Enforcement (Two Layers)

**Layer 1 — Database:** The `_transition()` method MUST NOT create a job row when the target status is in `{WAITING_PAPER_APPROVAL, WAITING_DATA_REVIEW, CLARIFYING, DRAFT, COMPLETED, FAILED, PAUSED}`. The Worker should never find a job for these stages, but the database constraint is the safety net.

**Layer 2 — Worker:** The StageRegistry has no handlers registered for human-wait stages. If a job for one somehow exists, the Worker treats it as a `FatalError` (do not retry, do not silently skip — log and dead-letter).

### 6.3 Job State Transitions

```
Current      → Target        Trigger                         Checks
────────────────────────────────────────────────────────────────────────
QUEUED       → RUNNING       claim_next_job                  —
RUNNING      → COMPLETED     complete_job                    worker_id + lease_token match
RUNNING      → FAILED        fail_job                        worker_id + lease_token match
RUNNING      → RETRY_WAIT    retry_job                       worker_id + lease_token match
RUNNING      → QUEUED        lease_expires_at < now           (done by claim_next_job)
RETRY_WAIT   → RUNNING       next_retry_at <= now             (done by claim_next_job)
RETRY_WAIT   → DEAD_LETTER   attempts >= max_attempts         (done by claim_next_job or retry_job)
CANCELLED    → (terminal)    —                                —
DEAD_LETTER  → (terminal)    —                                —
```

### 6.4 Paper State Transitions

```
Current      → Target        Trigger
────────────────────────────────────────────────
CANDIDATE    → SELECTED      approve_papers (paper in selected_ids)
CANDIDATE    → (rejected)    approve_papers (paper not in selected_ids)  — selection_status = 'rejected'
SELECTED     → FETCHED       Worker: fulltext downloaded
SELECTED     → FAILED        Worker: fulltext unavailable after retries
FETCHED      → PARSED        Worker: PDF parsed successfully
FETCHED      → FAILED        Worker: PDF corrupt / unparseable
FETCHED      → DEGRADED      Worker: parsed but with errors (e.g. missing pages)
PARSED       → EXTRACTED     Worker: extraction complete
PARSED       → DEGRADED      Worker: extraction partially failed
EXTRACTED    → (final)       —
DEGRADED     → (final)       —
FAILED       → (final)       —
```

## 7. Database Schema

### 7.1 Migration System

Manual SQL files in `python-tools/migrations/`, executed in order. Table:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
```

Migration rules:
- Each `.sql` file is executed at most once, keyed by version number.
- Before executing any migration, the engine reads the file, computes SHA-256, and compares against the stored checksum for already-applied versions. If a previously-applied file's checksum has changed, the engine refuses to start — this prevents accidental modification of historical migrations.
- Pending migrations run inside `BEGIN IMMEDIATE` to ensure only one process (API or Worker, whichever starts first) applies them.

```
python-tools/migrations/
├── 001_initial.sql       # current schema extracted from existing _initialize()
└── 002_pipeline.sql      # Phase 1A additions
```

### 7.2 New Tables (002_pipeline.sql)

**schema_migrations:**
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
```

**artifacts (metadata only — files stored on disk):**
```sql
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    artifact_type TEXT NOT NULL,  -- 'pdf', 'parsed_document', 'extraction', 'report'
    format TEXT NOT NULL,          -- 'pdf', 'json', 'markdown'
    path TEXT NOT NULL,
    sha256 TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id, artifact_type);
```

**reports:**
```sql
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    format TEXT NOT NULL DEFAULT 'markdown',
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);
```

**job_attempts:**
```sql
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
```

### 7.3 Modified Columns (ALTER TABLE)

**jobs table — new columns:**
```sql
ALTER TABLE jobs ADD COLUMN worker_id TEXT;
ALTER TABLE jobs ADD COLUMN claimed_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_token TEXT;
ALTER TABLE jobs ADD COLUMN next_retry_at TEXT;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
ALTER TABLE jobs ADD COLUMN result_json TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON jobs(idempotency_key) WHERE status IN ('completed', 'running');
```

**papers table — new columns:**
```sql
ALTER TABLE papers ADD COLUMN paper_status TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE papers ADD COLUMN error_message TEXT;
ALTER TABLE papers ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE papers ADD COLUMN version_id TEXT NOT NULL DEFAULT 'unknown';
-- version_id uses WorkVersion enum: preprint, journal, corrigendum, unknown
```

**tasks table — new column:**
```sql
ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
```

### 7.4 SQLite Configuration

Every connection MUST set:
```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
```

The database file MUST be on local disk (not NFS/CIFS). WAL mode, foreign keys, and busy timeout are non-negotiable.

### 7.5 Optimistic Locking

All task state mutations use:
```sql
UPDATE tasks SET status = ?, revision = revision + 1, updated_at = ?
WHERE id = ? AND revision = ?
```
If `rowcount == 0`, the caller re-reads the task and retries.

## 8. Job Lease and Fencing

### 8.1 Lease Token

Every job claim generates a unique `lease_token` (random UUID). The Worker stores it alongside the job.

```python
@dataclass
class ClaimedJob:
    job_id: int
    task_id: str
    stage: str
    worker_id: str
    lease_token: str
    lease_expires_at: str
    payload: dict
```

### 8.2 Lease Renewal

Long-running stages (PDF parsing, model extraction) MUST renew their lease periodically. The Worker calls:

```python
store.renew_lease(job_id=job.id, worker_id=config.worker_id, lease_token=job.lease_token)
```

This extends `lease_expires_at` by the configured lease duration (default 300s). Renewal interval is configurable; default 30s (i.e., renew at least every 30s for stages expected to run >60s).

### 8.3 Fencing on Completion

`complete_job`, `retry_job`, and `fail_job` ALL require `(job_id, worker_id, lease_token)`. They verify:

```sql
UPDATE jobs SET status = 'completed', ...
WHERE id = ? AND worker_id = ? AND lease_token = ? AND status = 'running'
```

If `rowcount == 0`, the Worker's claim was invalidated (lease expired and another Worker re-claimed the job, or the lease_token doesn't match). The old Worker MUST discard its result — it no longer owns the job.

This prevents a slow Worker from overwriting a new Worker's result after its lease expired.

### 8.4 Claim Query

```sql
-- claim_next_job: atomically claim the oldest eligible job
UPDATE jobs SET
    status = 'running',
    worker_id = ?,
    claimed_at = ?,
    lease_expires_at = datetime(?, '+' || ? || ' seconds'),
    lease_token = ?,
    attempts = CASE WHEN status = 'retry_wait' THEN attempts + 1 ELSE attempts END
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'queued'
       OR (status = 'running' AND lease_expires_at < datetime('now'))
       OR (status = 'retry_wait' AND next_retry_at <= datetime('now'))
    ORDER BY id LIMIT 1
)
RETURNING *
```

Note: `attempts` counts executions (not retries). Maximum 3 executions: first attempt, +5s retry, +30s retry, then dead_letter.

## 9. Transaction Boundaries

### 9.1 Stage Completion Transaction

When a stage handler completes successfully, the following MUST be in ONE database transaction:

1. Save stage result (papers inserted/updated, extraction recorded, artifact row created).
2. Update per-paper statuses for affected papers.
3. Complete the current job (status → 'completed', result_json set).
4. Insert a `job_attempts` row (finished_at, result_json).
5. If the next pipeline stage is automated: create the next job (status → 'queued').
6. If the next pipeline stage is a human gate: transition task to wait state WITHOUT creating a job.
7. Increment task `revision`.

All-or-nothing. If the process crashes mid-transaction, SQLite rolls back to the pre-stage state — the job returns to its previous status and can be reclaimed.

### 9.2 Atomic Artifact Writes

File writes use atomic rename:

```text
1. Write content to <path>.tmp.<uuid>
2. fsync / FlushFileBuffers
3. os.rename(<path>.tmp.<uuid>, <path>)
4. INSERT INTO artifacts (path, sha256, ...) inside the stage transaction
```

Do NOT write directly to the final path. Do NOT insert the artifacts row before the file is safely on disk. Do NOT store PDFs, full parsed JSON, or report content as BLOBs in SQLite.

## 10. Idempotency

### 10.1 Key Definition

```text
idempotency_key = "{task_id}:{stage}:{input_version}"
```

Where `input_version` is the version identifier for the inputs to this stage. It is a deterministic hash of the stage-relevant inputs (e.g. for extraction: `sha256(task_id + paper_id + parsed_document_version)`; for search: `sha256(task_id + definition_hash)`). The term `input_hash` may be used interchangeably with `input_version` — they refer to the same value.

### 10.2 UNIQUE Constraint

The `jobs` table has a partial unique index:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON jobs(idempotency_key) WHERE status IN ('completed', 'running');
```

This enforces at the database level that two jobs with the same idempotency key cannot both be active or completed.

### 10.3 Semantic Rules

| Scenario | Behavior |
|----------|----------|
| Same key, previous job COMPLETED | Reuse cached result from `result_json`. Do not re-execute. |
| Same key, previous job RUNNING | Do not create a duplicate job. Wait or return "in progress". |
| Same key, previous job FAILED/RETRY_WAIT | Retry the same job row (new `job_attempts` row, same `job.id`). Do NOT create a new job row. |
| User rolled back and modified definition | The new definition produces a different `input_version` → new `idempotency_key` → new job. Old results are versioned, not overwritten. |
| Admin changes validation rules | Phase 1A: this does NOT invalidate cached results. Phase 1B should add a `rule_version` to the idempotency key. |

### 10.4 Retry Semantics

`attempts` counts total executions, NOT retries:
- `attempts = 0`: never started
- `attempts = 1`: first execution (just claimed)
- `attempts = 2`: first retry (after 5s delay)
- `attempts = 3`: second retry (after 30s delay)
- `attempts >= max_attempts` (default 3): → `DEAD_LETTER`

Retry delays:
```
After attempts=1 failure → next_retry_at = now + 5s
After attempts=2 failure → next_retry_at = now + 30s
After attempts=3 failure → status = 'dead_letter'
```

## 11. Artifact Storage

### 11.1 Directory Layout

```
data/tasks/<task_id>/
├── search/
│   └── results.json
├── papers/
│   ├── <paper_id>/
│   │   ├── fulltext.pdf
│   │   └── metadata.json
├── parsed/
│   └── <paper_id>.json
├── extractions/
│   └── <paper_id>_v<version>.json
└── reports/
    └── v<version>.md
```

### 11.2 Atomic Write Pattern

```python
def atomic_write(path: str, content: str | bytes) -> str:
    tmp_path = f"{path}.tmp.{uuid.uuid4().hex}"
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(tmp_path, mode) as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp_path, path)  # atomic on same filesystem
    return path
```

### 11.3 Database Tracking

Every artifact file has a corresponding row in the `artifacts` table. Reports additionally have a `reports` table row. The database stores paths and hashes — never file contents.

## 12. Pipeline Contracts

```python
# pipeline/contracts.py

from typing import Protocol, runtime_checkable

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

## 13. Worker Design

### 13.1 Main Loop

```python
# workflow_worker.py — separate process, direct DB access

class WorkflowWorker:
    def __init__(self, store: WorkflowStore, registry: StageRegistry, config: WorkerConfig):
        self.store = store
        self.registry = registry
        self.config = config

    async def run(self):
        while True:
            job = self.store.claim_next_job(
                worker_id=self.config.worker_id,
                lease_duration=self.config.lease_duration,
            )
            if not job:
                await asyncio.sleep(self.config.poll_interval)
                continue

            try:
                # Start periodic renewal for long-running stages
                renewal_task = asyncio.create_task(self._renew_loop(job))
                handler = self.registry.get(job.stage)
                result = await handler.run(job, self.store)
                renewal_task.cancel()
                self.store.complete_job(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    lease_token=job.lease_token,
                    result=result,
                )
            except RetryableError as e:
                renewal_task.cancel()
                self.store.retry_job(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    lease_token=job.lease_token,
                    error=str(e),
                )
            except FatalError as e:
                renewal_task.cancel()
                self.store.fail_job(
                    job_id=job.id,
                    worker_id=self.config.worker_id,
                    lease_token=job.lease_token,
                    error=str(e),
                )

    async def _renew_loop(self, job: ClaimedJob):
        """Renew lease every renew_interval seconds for long-running stages."""
        while True:
            await asyncio.sleep(self.config.renew_interval)
            renewed = self.store.renew_lease(
                job_id=job.id,
                worker_id=self.config.worker_id,
                lease_token=job.lease_token,
                lease_duration=self.config.lease_duration,
            )
            if not renewed:
                # Lease was invalidated — another Worker claimed this job
                raise LeaseLostError(f"Lease lost for job {job.id}")
```

### 13.2 Initial Constraints

- `concurrency = 1` (single Worker, single thread). Required while there is only one 35B model instance and one GPU.
- Worker directly accesses the same SQLite database as the API — no HTTP calls.
- Worker does NOT use `X-Worker-Key` or any HTTP authentication. Its credential is the database access itself.

### 13.3 Stage Registry

```python
class StageRegistry:
    def __init__(self, config: WorkerConfig):
        self._handlers: dict[str, StageHandler] = {}

    def register(self, stage: str, handler: StageHandler):
        if stage in HUMAN_WAIT_STAGES:
            raise ValueError(f"Cannot register handler for human-wait stage: {stage}")
        self._handlers[stage] = handler

    def get(self, stage: str) -> StageHandler:
        if stage not in self._handlers:
            raise FatalError(f"No handler registered for stage: {stage}")
        return self._handlers[stage]

HUMAN_WAIT_STAGES = {
    "WAITING_PAPER_APPROVAL",
    "WAITING_DATA_REVIEW",
    "CLARIFYING",
    "DRAFT",
    "COMPLETED",
    "FAILED",
    "PAUSED",
}
```

## 14. API Changes (Phase 1A Minimum)

### 14.1 Auth Hardening

```python
# Test credentials (APP_ENV=test)
TEST_USERS = {
    "alice": {"password": "test-pass", "role": "user"},
    "bob": {"password": "test-pass", "role": "user"},
    "admin": {"password": "admin-pass", "role": "admin"},
}
```

- `POST /api/auth/token` requires `{"user_id": str, "password": str}` and validates against the credential store.
- JWT payload includes `{"sub": user_id, "role": role}`.
- No `X-Worker-Key` — Worker does not call the API.
- Ownership checks: `get_task`, `list_papers`, `list_extractions` enforce `owner_id == actor_id or is_admin`.

### 14.2 Phase 1A Endpoints

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/api/auth/token` | Login with credentials | Public |
| POST | `/api/research/tasks` | Create task | User JWT |
| GET | `/api/research/tasks` | List my tasks | User JWT |
| GET | `/api/research/tasks/{id}` | Task detail + papers + extractions + paper_status | Owner/admin |
| PUT | `/api/research/tasks/{id}/definition` | Update constraints | Owner |
| POST | `/api/research/tasks/{id}/start` | Start search | Owner |
| POST | `/api/research/tasks/{id}/candidates` | Submit candidates (Worker writes via DB) | Owner |
| POST | `/api/research/tasks/{id}/papers/approve` | Approve selected papers | Owner |
| GET | `/api/research/tasks/{id}/reports` | Get latest report | Owner/admin |
| GET | `/api/research/tasks/{id}/reports/{version}` | Get specific report version | Owner/admin |
| POST | `/api/research/tasks/{id}/review/request` | Request data review | Owner |
| POST | `/api/research/tasks/{id}/review` | Approve/reject extractions | Owner/admin |
| POST | `/api/research/tasks/{id}/pause` | Pause task | Owner |
| POST | `/api/research/tasks/{id}/resume` | Resume task | Owner |
| POST | `/api/research/tasks/{id}/rollback` | Rollback to earlier stage | Owner |
| GET | `/api/research/tasks/{id}/events` | Audit trail | Owner/admin |

Note: `/advance` and `/extractions` submission are removed from the user-facing API. These are now performed by the Worker via direct database access. In Phase 1A, the `/candidates` endpoint is called by the Worker directly through the store (not via HTTP), but the route remains available for manual testing during development and for Phase 2A's test harness.

## 15. Mock Adapters (Phase 2A)

### 15.1 MockSearchProvider

Reads from `fixtures/papers.json`. Returns 10 papers covering:
- Performance benchmark paper
- Structure paper
- Lab process paper
- Composition/ratio paper
- Authoritative validation paper
- Duplicate DOI
- Missing-abstract paper
- Lab-infeasible process
- Fulltext-unavailable paper
- Multi-role paper

### 15.2 MockDocumentParser

Uses PyMuPDF to parse `fixtures/sample_material.pdf` (a hand-crafted test PDF with known content, page numbers, figure captions, and a table). Outputs `ParsedDocument` with `ParsedBlock` entries including `block_id`, page numbers, and block types. For papers not in the fixture, returns a canned `ParsedDocument`.

### 15.3 MockAgentAdapter

Returns fixed `SampleExtraction` results with field-level evidence:
- Sample S1: explicit ratios (from Table 2, page 5), explicit process (from Section 2.3, page 4), estimated performance (from Figure 3, page 7)
- Sample S2: explicit ratios, derived performance, inferred curing time
- One field uses `source_type: "missing"` to test degradation handling

Each `CompositionRatio`, `ProcessStep`, and `PerformanceMetric` carries `evidence_ids` linking to specific `EvidenceLocator` entries with `field_path`.

### 15.4 ReportGenerator

Produces a Markdown report with 10 fixed sections:

1. 研究目标和约束
2. 候选论文列表
3. 目标性能
4. 候选结构
5. 实验室可行工艺体系
6. 成分和配比
7. 配方—工艺—性能表
8. 数据缺失和冲突提示
9. 下一批实验点
10. 引用列表

Saved atomically to `data/tasks/<task_id>/reports/v1.md`.

### 15.5 Abstract-Only Degradation

Papers without full text are marked `is_abstract_only = True` and `paper_status = DEGRADED`. Their extractions carry `source_type: "missing"` or `"inferred"` on all fields. The report section "数据缺失和冲突提示" must flag them. They MUST NOT be included in any quantitative comparison (e.g., ranking materials by performance).

## 16. E2E Test Suite (Phase 2A)

### 16.1 Test Infrastructure

- Each test creates a temporary SQLite database.
- Fixtures are copied to a temp directory.
- Worker runs in the same process as the test (synchronous polling loop).
- Default lease timeout shortened to 5s for tests.
- `scripts/run_basic_test.sh` orchestrates all tests and reports results.

### 16.2 Test Scenarios

| # | Test | What It Validates |
|---|------|-------------------|
| 1 | **Mock happy path** | Full pipeline: create → clarify → search → screen → approve → fetch → parse → extract → validate → report → review → COMPLETED. All transitions Worker-driven. |
| 2 | **User isolation** | Alice cannot access Bob's tasks, papers, extractions, or reports. |
| 3 | **Human gates — owner and admin** | Both task owner and admin can approve papers and review extractions. Non-owner, non-admin is rejected. |
| 4 | **Human gates — no premature job** | After candidates submitted, no FETCHING_FULLTEXT job exists until owner approves papers. |
| 5 | **Worker crash + lease expiry** | Kill Worker mid-PARSING. Wait for lease expiry. New Worker claims and continues. Old Worker's result (if any) is fenced. |
| 6 | **Lease fencing** | Simulate slow Worker: manually change lease_token, verify old Worker's complete_job returns rowcount=0. |
| 7 | **Single paper degradation** | 3 papers: 1 OK, 1 PDF corrupt (→FAILED), 1 partial parse (→DEGRADED). Task continues, report marks evidence levels. |
| 8 | **Idempotent replay** | Execute same stage twice with same idempotency_key. Verify no duplicate papers, extractions, or artifacts. |
| 9 | **Concurrent migration** | Start API and Worker simultaneously (separate threads, same DB). Verify schema_migrations has exactly one row per version. |
| 10 | **Mixed evidence per field** | Verify that one paper's extraction has `explicit` ratios AND `estimated` performance AND `missing` modulus — all in the same SampleExtraction, tracked per-field via `evidence_ids`. |
| 11 | **Abstract-only degradation** | Paper with no fulltext → `is_abstract_only = True`, `paper_status = DEGRADED`. Does not appear in quantitative comparison tables. |
| 12 | **Rollback with new input** | Rollback to CLARIFYING, modify definition, restart. Verify new `input_version` → new `idempotency_key` → new job. Old results preserved (not overwritten). |

## 17. Acceptance Criteria

1. `bash scripts/run_basic_test.sh` completes with exit code 0.
2. No IEEE/WoS credentials needed.
3. No real 35B model needed.
4. Mock E2E task reaches COMPLETED through Worker-driven transitions (no `/advance` shortcuts).
5. Alice cannot see Bob's tasks (test #2).
6. Human gates actually pause — no job created for wait states (test #4).
7. Service restart preserves all data (task, papers, extractions, report).
8. Single paper failure degrades that paper, does not block others (test #7).
9. All data fields have per-evidence `source_type` via `evidence_ids` (test #10).
10. Report is regeneratable from stored extraction data.
11. All tests use temporary databases — no pollution of production data.
12. No lingering processes after test cleanup.
13. Concurrent API+Worker startup runs migration exactly once (test #9).
14. Old Worker cannot overwrite new Worker's result after lease loss (test #6).

## 18. Out of Scope (deferred past Phase 2B)

- Real IEEE/WoS/Crossref integration
- Real 35B model (Pi Agent for extraction)
- Real embedding model (BGE/local)
- Production auth (OAuth, LDAP, password hashing)
- Full admin dashboard UI
- CORS hardening for production
- Advanced PDF (SEM/TEM images, curve digitization, OCR for scanned PDFs)
- Complex cross-paper comparability normalization
- Production monitoring, logging, alerting
- Disk quota, PDF size limits, rate limiting
- Worker-to-API internal HTTP RPC
- Worker horizontal scaling (multi-worker)

## 19. Risks

| Risk | Mitigation |
|------|-----------|
| Data model needs major revision after real model integration | Models focus on structural hierarchy and evidence linking — adding new field types is additive |
| SQLite concurrency limits | Phase 1A uses single Worker; WAL mode + busy_timeout=30s; migration path to PostgreSQL protected by Protocol interfaces |
| Mock adapters diverge from real interfaces | Contracts are Protocol-based; mock and real must satisfy the same Protocol |
| Lease fencing missing edge cases | Lease token verified on every mutation; `rowcount == 0` check prevents stale writes; test #6 explicitly validates |
| Field-level evidence adds complexity | It is the foundation for all downstream comparability and confidence decisions; without it, extracted data cannot be trusted for quantitative analysis |
