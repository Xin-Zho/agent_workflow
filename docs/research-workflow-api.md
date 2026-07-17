# Research workflow API

The workflow API is available even when the Pi model process is offline. State is stored
in SQLite at `data/workflow.db` by default; set `WORKFLOW_DB_PATH` to override it.

Use `Authorization: sk-dev` for local development. Set `DEV_API_KEY` before exposing the
service outside localhost.

## Minimal lifecycle

1. `POST /api/research/tasks` — create a task in `CLARIFYING`.
2. `PUT /api/research/tasks/{id}/definition` — save confirmed constraints and coverage.
3. `POST /api/research/tasks/{id}/start` — enqueue the `SEARCHING` stage.
4. `POST /api/research/tasks/{id}/candidates` — store screened abstracts and wait for approval.
5. `POST /api/research/tasks/{id}/papers/approve` — select papers and enqueue full-text work.
6. `POST /api/research/tasks/{id}/advance` — worker advances deterministic automated stages.
7. `POST /api/research/tasks/{id}/extractions` — version an extraction result.
8. `POST /api/research/tasks/{id}/review/request` — request manual data review.
9. `POST /api/research/tasks/{id}/review` — owner/admin approves or rejects extracted data.

Tasks can be paused, resumed or rolled back without deleting intermediate records. Audit
events are available from `GET /api/research/tasks/{id}/events`.

## Example

```bash
curl -X POST http://127.0.0.1:8000/api/research/tasks \
  -H 'Authorization: sk-dev' \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "快速迭代柔性材料体系",
    "query": "寻找成熟工艺并优化体系内成分和配比"
  }'
```

The response contains the task ID and the complete clarification-question list.

## Queue contract

The SQLite `jobs` table is FIFO. A future Pi worker/search connector claims the oldest
queued job, performs the stage and calls the API with its results. One paper failure must
be recorded and degraded independently instead of failing the entire task.
