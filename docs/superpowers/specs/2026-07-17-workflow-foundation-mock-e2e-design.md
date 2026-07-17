# Workflow Foundation + Mock E2E — Design Spec

**Date:** 2026-07-17
**Status:** draft
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

## 2. Goals

1. A **Worker process** that consumes FIFO jobs and advances tasks through the full pipeline.
2. **Mock adapters** for all external capabilities (search, embedding, agent, PDF parsing) so the pipeline can run without IEEE/WoS/35B model access.
3. A **single-command E2E test** that exercises the full happy path with mock data.
4. **Data integrity**: idempotent stages, job leases, per-paper status, artifact persistence.
5. **Basic auth**: users cannot impersonate each other; Worker has a separate credential.
6. **Human gates**: `WAITING_PAPER_APPROVAL` and `WAITING_DATA_REVIEW` are never auto-advanced.

Non-goals for this spec: production auth (OAuth/LDAP), real IEEE/WoS integration, real 35B model calls, advanced PDF OCR/curve digitization, full admin dashboard, CORS hardening.

## 3. Milestones

### Phase 1A — Minimal Foundation

| # | Deliverable | Files |
|---|------------|-------|
| 1 | Structured data models (Pydantic) | `workflow_models.py` (new) |
| 2 | Database migrations + schema_version | `migrations/001_initial.sql`, `002_pipeline.sql` (new); `workflow_engine.py` (modify) |
| 3 | Job lease mechanism (worker_id, claimed_at, lease_expires_at, idempotency_key) | `workflow_engine.py` (modify) |
| 4 | Worker framework (claim → dispatch → complete/fail/retry) | `workflow_worker.py` (new) |
| 5 | Pipeline contracts (Protocol classes) | `pipeline/contracts.py` (new) |
| 6 | Artifact storage (filesystem + DB tracking) | `workflow_engine.py` (modify) |
| 7 | Minimal per-paper status (7 states) | `workflow_engine.py` (modify) |
| 8 | Input validation (Pydantic models enforced at API boundary) | `workflow_api.py` (modify) |
| 9 | Human-gate constraints (Worker cannot claim human-wait stages) | `workflow_worker.py`, `workflow_engine.py` (modify) |

### Phase 2A — Mock Happy-Path E2E

| # | Deliverable | Files |
|---|------------|-------|
| 10 | Mock SearchProvider | `adapters/mock_search.py` (new) |
| 11 | Mock EmbeddingRetriever | `adapters/mock_embedding.py` (new) |
| 12 | Mock Reranker | `adapters/mock_embedding.py` |
| 13 | Mock FulltextProvider | `adapters/mock_search.py` |
| 14 | Mock DocumentParser | `adapters/mock_parser.py` (new) |
| 15 | Mock AgentAdapter | `adapters/mock_agent.py` (new) |
| 16 | Markdown ReportGenerator | `pipeline/report_stage.py` (new) |
| 17 | Stage handlers (search, screening, parse, extract, validate, report) | `pipeline/*.py` (new) |
| 18 | Happy-path E2E test | `tests/test_e2e_basic.py` (new) |
| 19 | Test fixtures (papers.json, sample PDF, mock extraction) | `fixtures/` (new) |

### Phase 1B — Foundation Hardening

| # | Deliverable |
|---|------------|
| 20 | Auth hardening (credentials, JWT role, Worker credential, ownership checks) |
| 21 | Full per-paper status (14 states) with error tracking |
| 22 | Validation stage (unit checks, ratio basis, page/figure locator, inferred-value guard) |
| 23 | Rollback constraints (direction check, artifact marking, human-gate re-entry) |
| 24 | Job recovery (heartbeat, lease expiry, exponential backoff, max retry, dead letter) |
| 25 | User memory isolation (per-user MemoryManager, per-task context, reviewed-only sharing) |

### Phase 2B — Resilience & Multi-User E2E

| # | Test Scenario |
|---|--------------|
| 26 | Single paper failure — others continue, report marks evidence level |
| 27 | Worker crash mid-stage — lease expires, new Worker resumes |
| 28 | Two users — isolation, FIFO fairness, no cross-access |
| 29 | Human gates — Worker waits, owner reviews, admin reviews, non-owner blocked |
| 30 | Idempotent replay — same job twice produces no duplicates |

## 4. Architecture

```
┌─ web_api_server.py ────────────────────────────────────────┐
│  FastAPI app                                                 │
│  ├─ /api/auth/*          (JWT with role)                     │
│  ├─ /api/research/*       (workflow_api.py)                  │
│  └─ /ws, /api/chat/*      (Pi RPC, per-session queues)      │
└────────────────────────────┬────────────────────────────────┘
                             │ SQLite only (no direct calls)
                             ▼
┌─ workflow_engine.py ──────┐    ┌─ workflow_worker.py ───────┐
│  WorkflowStore              │    │  while True:                │
│  ├─ migrations/             │    │    job = store.claim(wid)   │
│  ├─ tasks, papers           │    │    handler = registry[stage]│
│  ├─ extractions, artifacts  │    │    result = handler(job)    │
│  ├─ jobs (with leases)      │    │    store.complete(id, res)  │
│  └─ schema_version          │    │  (separate process)         │
└─────────────────────────────┘    └─────────┬──────────────────┘
                                             │
                                             ▼
                                    ┌─ pipeline/ ──────────────┐
                                    │  contracts.py (Protocols) │
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

**API ↔ Worker boundary:** Communication only through the SQLite database. API creates jobs by changing task status. Worker claims and completes jobs. Each can restart independently.

**Pipeline ↔ Adapter boundary:** Stage handlers depend on Protocol interfaces, never concrete adapters. Adapter selection is configuration-driven.

## 5. Data Models

### 5.1 Core Models (workflow_models.py)

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
    target_range: str | None = None  # e.g. ">200 MPa", "<5 W/m·K"

class EvidenceLocator(BaseModel):
    work_id: str
    file_version: str
    page: int
    section: str | None = None
    figure: str | None = None
    table: str | None = None
    source_type: Literal["explicit", "derived", "estimated", "inferred", "missing"]

class MaterialComponent(BaseModel):
    name: str
    role: str | None = None  # matrix, filler, solvent, catalyst, ...
    supplier: str | None = None

class CompositionRatio(BaseModel):
    component: str
    raw_value: str           # original expression
    raw_unit: str            # wt%, vol%, mol%, phr, ...
    normalized_value: float | None = None
    normalized_unit: str | None = None

class ProcessStep(BaseModel):
    step_number: int
    description: str
    parameters: dict[str, Any]  # temperature, time, pressure, atmosphere, ...
    equipment: str | None = None

class TestCondition(BaseModel):
    property: str
    method: str | None = None
    standard: str | None = None  # ASTM, ISO, GB/T, ...
    parameters: dict[str, Any]

class PerformanceMetric(BaseModel):
    property: str
    value: str                # raw expression
    unit: str
    test_condition: str | None = None
    evidence: EvidenceLocator

class SampleExtraction(BaseModel):
    sample_id: str
    components: list[MaterialComponent]
    ratios: list[CompositionRatio]
    process_steps: list[ProcessStep]
    test_conditions: list[TestCondition]
    performance_metrics: list[PerformanceMetric]
    evidence: list[EvidenceLocator]

class ParsedPage(BaseModel):
    page_number: int
    text: str
    captions: list[str]
    tables: list[dict[str, Any]]  # ParsedTable

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

### 5.2 Paper Status (7 states for Phase 1A)

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

### 5.3 Job States

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

## 6. Database Schema

### 6.1 Migration System

Manual SQL files in `python-tools/migrations/`, executed in order by a `_run_migrations()` method in WorkflowStore. The `schema_version` table tracks which migrations have been applied.

```
python-tools/migrations/
├── 001_initial.sql       # existing schema (extracted from current _initialize)
└── 002_pipeline.sql      # Phase 1A additions
```

### 6.2 New Tables (002_pipeline.sql)

**schema_version:**
```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
```

**artifacts:**
```sql
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    artifact_type TEXT NOT NULL,  -- pdf, parsed_document, extraction, report
    format TEXT NOT NULL,          -- pdf, json, markdown
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
    attempt_num INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT,
    error TEXT
);
```

### 6.3 Modified Columns (ALTER TABLE)

**jobs table — new columns:**
```sql
ALTER TABLE jobs ADD COLUMN worker_id TEXT;
ALTER TABLE jobs ADD COLUMN claimed_at TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE jobs ADD COLUMN next_retry_at TEXT;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
ALTER TABLE jobs ADD COLUMN result_json TEXT;
```

**papers table — new columns:**
```sql
ALTER TABLE papers ADD COLUMN paper_status TEXT NOT NULL DEFAULT 'candidate';
ALTER TABLE papers ADD COLUMN error_message TEXT;
ALTER TABLE papers ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE papers ADD COLUMN version_id TEXT NOT NULL DEFAULT 'v1';
```

**tasks table — new column:**
```sql
ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
```

### 6.4 Optimistic Locking

All task status updates use:
```sql
UPDATE tasks SET status=?, revision=revision+1, updated_at=?
WHERE id=? AND revision=?
```
If rowcount == 0, the caller retries with a fresh read.

## 7. Pipeline Contracts

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

## 8. Worker Design

### 8.1 Main Loop

```python
# workflow_worker.py

class WorkflowWorker:
    def __init__(self, store: WorkflowStore, registry: StageRegistry, config: WorkerConfig):
        self.store = store
        self.registry = registry
        self.config = config

    async def run(self):
        while True:
            job = self.store.claim_next_job(worker_id=self.config.worker_id)
            if not job:
                await asyncio.sleep(self.config.poll_interval)
                continue

            try:
                handler = self.registry.get(job["stage"])
                result = await handler.run(job, self.store)
                self.store.complete_job(job["id"], result=result)
            except RetryableError as e:
                self.store.retry_job(job["id"], error=str(e))
            except FatalError as e:
                self.store.fail_job(job["id"], error=str(e))
```

### 8.2 Job Claiming (Lease-Based)

```sql
-- claim_next_job: atomically claim the oldest eligible job
UPDATE jobs SET
    status = 'running',
    worker_id = ?,
    claimed_at = ?,
    lease_expires_at = datetime(?, '+300 seconds'),
    attempts = attempts + 1
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'queued'
       OR (status = 'running' AND lease_expires_at < datetime('now'))
       OR (status = 'retry_wait' AND next_retry_at <= datetime('now'))
    ORDER BY id LIMIT 1
)
RETURNING *
```

Lease duration: 300 seconds (configurable). If the Worker crashes, the lease naturally expires and another Worker claims the job.

### 8.3 Stage Registry

```python
class StageRegistry:
    def __init__(self, config: WorkerConfig):
        self._handlers: dict[str, StageHandler] = {}
        # Populated from config — Phase 2A wires mock adapters here

    def get(self, stage: str) -> StageHandler:
        if stage not in self._handlers:
            raise FatalError(f"No handler for stage: {stage}")
        return self._handlers[stage]
```

**Human-gate stages** (WAITING_PAPER_APPROVAL, WAITING_DATA_REVIEW, CLARIFYING, DRAFT, COMPLETED, FAILED, PAUSED) are never registered as stage handlers. If a job somehow gets created for one, the Worker treats it as a fatal error.

### 8.4 Idempotency

Each stage handler computes:
```python
idempotency_key = f"{task_id}:{stage}:{input_version}"
```

Before executing, the handler checks if a completed `job_attempts` row exists with a matching `idempotency_key` and `result_json IS NOT NULL`. If so, it returns the cached result. This prevents:
- Duplicate papers on retry
- Duplicate extraction versions
- Duplicate report generations

### 8.5 Retry Policy

| Attempt | Delay |
|---------|-------|
| 1→2 | 5s |
| 2→3 | 30s |
| 3→dead_letter | — |

`max_attempts` defaults to 3, configurable per stage.

## 9. Artifact Storage

### 9.1 Directory Layout

```
data/tasks/<task_id>/
├── search/
│   └── results.json
├── papers/
│   ├── <paper_id>/
│   │   ├── fulltext.pdf
│   │   └── metadata.json
├── parsed/
│   └── <paper_id>.json          # ParsedDocument
├── extractions/
│   └── <paper_id>_v<version>.json  # SampleExtraction[]
└── reports/
    └── v<version>.md
```

### 9.2 Database Tracking

Every file written to disk has a corresponding row in the `artifacts` table with `path`, `sha256`, `artifact_type`, and `version`. Reports additionally have a `reports` table row.

## 10. API Changes

### 10.1 Auth Hardening (Phase 1A minimum)

```python
# Test credentials (APP_ENV=test)
TEST_USERS = {
    "alice": {"password": "test-pass", "role": "user"},
    "bob": {"password": "test-pass", "role": "user"},
    "admin": {"password": "admin-pass", "role": "admin"},
}
WORKER_API_KEY = os.environ.get("WORKER_API_KEY", "sk-worker-dev")
```

- `POST /api/auth/token` now requires `{"user_id": str, "password": str}` and validates against the credential store.
- JWT payload includes `{"sub": user_id, "role": role}`.
- **Worker authentication**: The Worker does NOT use JWT. It authenticates with the `X-Worker-Key` header (value: `WORKER_API_KEY`). This cleanly separates Worker credentials from user credentials and makes it trivial to rotate the Worker key independently.
- Worker-only endpoints (`/advance`, record extraction submission) check `X-Worker-Key` header, not JWT `Authorization` header.
- Ownership checks: `get_task`, `list_papers`, `list_extractions` enforce `owner_id == actor_id or is_admin`.

### 10.2 New/Modified Endpoints

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/api/auth/token` | Login with credentials | Public |
| GET | `/api/research/tasks/{id}` | Now includes paper_status per paper | Owner/admin |
| GET | `/api/research/tasks/{id}/reports` | Get latest report | Owner/admin |
| GET | `/api/research/tasks/{id}/reports/{version}` | Get specific report version | Owner/admin |
| POST | `/api/research/tasks/{id}/advance` | Restricted to Worker API key | Worker |
| POST | `/api/research/tasks/{id}/extractions` | Restricted to Worker API key | Worker |

### 10.3 Worker-Only Endpoints

Phase 1A moves `/advance` and `/extractions` (submission) behind a Worker credential check. The Worker authenticates with `WORKER_API_KEY` rather than a user JWT.

## 11. Mock Adapters (Phase 2A)

### 11.1 MockSearchProvider

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

### 11.2 MockDocumentParser

Uses PyMuPDF to parse `fixtures/sample_material.pdf` (a hand-crafted test PDF with known content, page numbers, figure captions, and a table). For papers not in the fixture, returns a canned `ParsedDocument`.

### 11.3 MockAgentAdapter

Returns fixed `SampleExtraction` results with known evidence locators, including:
- 2 samples with explicit ratios
- 1 sample with an estimated performance value
- 1 sample with mixed evidence levels per field

### 11.4 ReportGenerator

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

Saved to `data/tasks/<task_id>/reports/v1.md`.

## 12. E2E Test (Phase 2A)

### 12.1 Happy Path (`test_e2e_basic.py`)

```text
1. Alice creates task "柔性导电材料配方优化"
2. Alice confirms research constraints
3. Alice starts search → Worker picks up SEARCHING job
4. MockSearch returns 10 candidates
5. Screening filters, ranks → enters WAITING_PAPER_APPROVAL
6. Alice selects 3 papers → worker picks up FETCHING_FULLTEXT
7. MockParser parses each paper
8. Worker advances through PARSING → READING → EXTRACTING
9. MockAgent returns extractions for 3 papers
10. Worker advances through VALIDATING → GENERATING_REPORT
11. Report saved to disk → enters WAITING_DATA_REVIEW
12. Admin approves → COMPLETED
13. Verify: events complete, artifacts exist, report readable, no duplicates
```

### 12.2 Test Infrastructure

- Each test creates a temporary SQLite database.
- Fixtures are copied to a temp directory.
- Worker runs in the same process as the test (synchronous polling loop).
- Default lease timeout shortened to 5s for tests.
- `run_basic_test.sh` orchestrates: setup temp env → unit tests → API tests → Worker tests → E2E test → report.

## 13. Acceptance Criteria

1. `bash scripts/run_basic_test.sh` completes with exit code 0.
2. No IEEE/WoS credentials needed.
3. No real 35B model needed.
4. Mock E2E task reaches COMPLETED through Worker-driven transitions.
5. Alice cannot see Bob's tasks.
6. Human gates actually pause (Worker does not claim those stages).
7. Service restart preserves all data (task, papers, extractions, report).
8. Single paper parse failure degrades that paper, does not block others.
9. All extractions have per-evidence `source_type`.
10. Report is regeneratable from stored extraction data.
11. All tests use temporary databases — no pollution of production data.
12. No lingering processes after test cleanup.

## 14. Out of Scope (deferred past Phase 2B)

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

## 15. Risks

| Risk | Mitigation |
|------|-----------|
| Data model needs major revision after real model integration | Keep models focused on structure (hierarchy), not exhaustive field coverage — add fields later |
| SQLite concurrency limits hit with multiple Workers | Phase 1A uses single Worker; WAL mode is enabled; migration path to PostgreSQL is protected by Protocol interfaces |
| Mock adapters diverge from real adapter interfaces | Contracts are Protocol-based; mock and real must satisfy the same Protocol — type checker catches divergence |
| Job lease mechanism has edge cases with very fast stages | Default lease is 300s; fast stages complete well within this; tests use shorter lease |
