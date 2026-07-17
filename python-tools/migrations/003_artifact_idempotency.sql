-- 003_artifact_idempotency.sql: content-addressed deduplication for artifacts
-- Prevents duplicate artifact rows when the same content is recorded more than once
-- (e.g., on pipeline retry after a successful parse).
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_content
ON artifacts(task_id, paper_id, artifact_type, sha256);
