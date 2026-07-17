"""Workflow Worker -- claims jobs from SQLite and executes pipeline stages.
Runs as a separate process with direct DB access (no HTTP to API)."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from workflow_engine import WorkflowStore
from workflow_config import WorkflowConfig

logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """Transient failure -- job should be retried."""

class FatalError(Exception):
    """Permanent failure -- job should go to dead_letter."""

class LeaseLostError(Exception):
    """Lease was invalidated -- discard result, another Worker owns this job."""


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
                logger.warning("Job %s lease lost -- discarding result", job["id"])
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
