# Fix #5 / #7 Implementation Report

## Status: COMPLETE

Both fixes implemented and verified. All 35 engine tests + 9 API tests pass. Additional smoke tests confirm the refactored `_promote_dead_letters()` works in both standalone and inline modes, and the new READING stage module loads and produces correct reading plans.

---

## Fix #5: Unify dead-letter promotion

### Changes (1 file)

**`python-tools/workflow_engine.py`**

1. **`_promote_dead_letters()` refactored** (line ~822) to accept optional `conn` and `now` parameters:
   - When `conn` is provided, executes the UPDATE on that connection (caller manages transaction).
   - When `conn` is `None` (default), opens its own connection, begins IMMEDIATE, commits, and closes.
   - `now` defaults to `db_utc_now()` when not provided.

2. **`claim_next_job()`** (line ~683): Replaced the inline dead-letter SQL with:
   ```python
   self._promote_dead_letters(conn=conn, now=now)
   ```

3. **`retry_job()`**: Already implemented — when `attempts >= max_attempts`, status is set to `'dead_letter'` directly (no changes needed).

### Verification
- `test_job_retry_and_dead_letter` (existing) passes.
- `test_retry_max_attempts_goes_directly_to_dead_letter` (existing) passes.
- Manual smoke test confirms both standalone and inline promotion work.

---

## Fix #7: Formal READING stage

### Changes (5 files)

1. **`python-tools/pipeline/reading_stage.py`** (NEW)
   - `run_reading_stage()` handler: iterates parsed papers, loads ParsedDocument artifacts, tags blocks via `_tag_block()`, generates reading_plan artifacts with `_build_reading_plan()`, advances to EXTRACTING.
   - `_tag_block(block)`: Maps block_type `"table"`/`"figure_caption"` directly; falls back to keyword matching for `formula`, `process`, `performance` tags.
   - `_build_reading_plan(doc)`: Returns `{block_groups: dict, focus_block_ids: list}` with priority ordering: tables > performance > process > formulas > figure_captions > other.
   - Idempotent on retry via existing reading_plan artifact detection.
   - Content-addressed artifact naming via SHA-256.

2. **`python-tools/pipeline/parse_stage.py`**
   - Advance target changed from `"EXTRACTING"` to `"READING"` (both occurrences).
   - Docstring updated to reflect the formal READING stage.

3. **`python-tools/workflow_engine.py`**
   - `advance()`: `PARSING: {READING}` (removed `EXTRACTING` fast-path).
   - `advance_for_worker()`: same change.

4. **`python-tools/workflow_worker.py`**
   - `AUTOMATED_STAGES`: added `"READING"` to the set.

5. **`python-tools/worker_main.py`**
   - Imported `run_reading_stage` from `pipeline.reading_stage`.
   - Registered `FunctionStageHandler("READING", ...)` between PARSING and EXTRACTING.

6. **`tests/test_workflow_engine.py`**
   - `test_worker_request_data_review`: Added `READING` to the stage advance sequence.

### Verification
- `_tag_block()` correctly tags: `table`, `figure_caption` by block_type; `performance` by keyword ("conductivity"); returns `None` for unrelated text.
- `_build_reading_plan()` correctly groups and orders blocks.
- All 35 engine tests pass, including `test_happy_path_with_manual_gates_and_admin_review` (which traverses through READING).
- All 9 API tests pass.

---

## Commits

```
<commit-hash> feat: implement Fix #5 (unify dead-letter promotion) and Fix #7 (formal READING stage)
```

- Fix #5: `_promote_dead_letters()` now accepts optional `conn`/`now` params; `claim_next_job()` delegates to it.
- Fix #7: New `reading_stage.py` handler; parse_stage advances to READING; READING is a required automated stage; registration in worker_main.py.

---

## Test Summary

| Suite | Count | Status |
|---|---|---|
| `test_workflow_engine.py` (WorkflowStoreTest) | 25 | All PASS |
| `test_workflow_engine.py` (WorkerContextAuthTest) | 10 | All PASS |
| `test_workflow_api.py` | 9 | All PASS |
| `test_workflow_models.py` | 1 error (pre-existing sys.path issue) | Unrelated |
| Manual smoke: reading stage logic | 5 checks | All PASS |
| Manual smoke: dead-letter promotion | 4 checks | All PASS |

---

## Concerns

1. **Pre-existing `test_workflow_models.py` import error**: The test file does not add `python-tools/` to `sys.path`, causing a `ModuleNotFoundError` for `workflow_models`. This predates these fixes and is not caused by these changes.

2. **`test_worker_request_data_review` test pattern**: The test advances stages using `advance_for_worker()` with the same `ctx` throughout, which means the lease from the original FETCHING_FULLTEXT job is reused. Each advance creates a new job but the original job's lease remains valid for lease verification. This works but is a latent concern for any future changes to lease verification.

3. **No new migration needed**: The schema already supports the `reading_plan` artifact type via the existing `artifacts` table (artifact_type is a free-text field).
