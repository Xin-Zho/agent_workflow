"""FETCHING_FULLTEXT stage: download PDFs for selected papers."""

import hashlib
import os
import uuid

from workflow_engine import WorkerContext, WorkflowStore
from artifact_utils import atomic_write_unique
from pipeline.contracts import FulltextProvider


async def run_fulltext_stage(
    ctx: WorkerContext,
    job: dict,
    store: WorkflowStore,
    provider: FulltextProvider,
) -> dict:
    """Execute the FETCHING_FULLTEXT pipeline stage.

    Iterates over selected papers, fetches PDFs via the FulltextProvider,
    writes them to disk as content-addressed artifacts, and advances the
    task to PARSING when complete.
    """
    task_id = job["task_id"]
    papers = store.list_papers_for_worker(ctx)
    selected = [p for p in papers if p.get("paper_status") == "selected"]

    if not selected:
        # Nothing to fetch -- advance directly
        store.advance_for_worker(ctx, "PARSING")
        return {"downloaded": 0, "degraded": 0}

    import workflow_models as wm

    downloaded = 0
    for paper in selected:
        meta = wm.PaperMetadata(
            work_id=paper["work_id"], title=paper["title"]
        )
        pdf_bytes = await provider.fetch(meta)
        if pdf_bytes:
            sha = hashlib.sha256(pdf_bytes).hexdigest()
            paper_dir = os.path.join(
                os.path.dirname(store.db_path),
                "..",
                "data",
                "tasks",
                task_id,
                "papers",
                paper["id"],
            )
            os.makedirs(paper_dir, exist_ok=True)
            path = os.path.join(paper_dir, f"fulltext_{sha[:16]}.pdf")
            atomic_write_unique(pdf_bytes, path)
            store.record_artifact(
                task_id, paper["id"], "pdf", "pdf", path, sha
            )
            store.update_paper_status(paper["id"], "fetched")
            downloaded += 1
        else:
            store.update_paper_status(
                paper["id"], "degraded", error="fulltext unavailable"
            )

    # Advance to PARSING
    store.advance_for_worker(ctx, "PARSING")
    return {"downloaded": downloaded, "degraded": len(selected) - downloaded}
