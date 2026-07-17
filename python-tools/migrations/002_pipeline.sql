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
