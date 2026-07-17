import os, sys, tempfile, asyncio
import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

from workflow_engine import WorkerContext, WorkflowStore, TaskStatus
from workflow_worker import WorkflowWorker, StageRegistry, StageHandler, LeaseLostError, RetryableError, FatalError
from workflow_config import WorkflowConfig


class FakeHandler(StageHandler):
    def __init__(self, result=None, should_fail=None):
        self.result = result or {"done": True}
        self.should_fail = should_fail
        self.call_count = 0

    async def run(self, job, store, ctx=None):
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
        ctx = WorkerContext(
            worker_id=config.worker_id,
            job_id=job["id"],
            task_id=job["task_id"],
            lease_token=job["lease_token"],
        )
        await handler.run(job, store, ctx)
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
        job = store.claim_next_job(config.worker_id, lease_duration=config.lease_duration)
        old_expiry = job["lease_expires_at"]
        await asyncio.sleep(0.1)
        ok = store.renew_lease(job["id"], config.worker_id, job["lease_token"], lease_duration=10)
        assert ok is True
        with store._connect() as conn:
            row = conn.execute("SELECT lease_expires_at FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            assert row["lease_expires_at"] > old_expiry
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
