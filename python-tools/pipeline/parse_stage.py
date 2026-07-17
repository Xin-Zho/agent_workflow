"""PARSING stage: parse downloaded PDFs into structured documents."""

import json
import os

from workflow_engine import WorkflowStore
from artifact_utils import atomic_write_unique
from pipeline.contracts import DocumentParser
from workflow_models import ParsedDocument


async def run_parse_stage(
    job: dict,
    store: WorkflowStore,
    parser: DocumentParser,
) -> dict:
    """Execute the PARSING pipeline stage.

    For each fetched paper, reads the PDF artifact, delegates to
    DocumentParser.parse(), saves the resulting ParsedDocument as a JSON
    artifact, and updates paper status. Advances the task to EXTRACTING
    when complete (fast-path skipping the unused READING stage).
    """
    task_id = job["task_id"]
    papers = store.list_papers(task_id, "worker")
    fetched = [p for p in papers if p.get("paper_status") == "fetched"]

    if not fetched:
        store.advance(task_id, "worker", "EXTRACTING")
        return {"parsed": 0, "degraded": 0}

    # Retrieve cached PDF artifacts for this task
    pdf_artifacts = store.get_artifacts(task_id, "pdf")
    pdf_by_paper = {a["paper_id"]: a for a in pdf_artifacts}

    parsed_count = 0
    degraded_count = 0
    for paper in fetched:
        pdf_artifact = pdf_by_paper.get(paper["id"])
        if pdf_artifact is None or not os.path.exists(pdf_artifact["path"]):
            store.update_paper_status(
                paper["id"], "degraded", error="pdf artifact not found"
            )
            degraded_count += 1
            continue

        try:
            with open(pdf_artifact["path"], "rb") as f:
                pdf_bytes = f.read()

            parsed: ParsedDocument = await parser.parse(pdf_bytes)
            # Ensure the work_id and a file version are set
            parsed.work_id = paper.get("work_id", parsed.work_id)
            if not parsed.file_version:
                parsed.file_version = "v1"

            # Write the parsed document as a JSON artifact
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

            json_bytes = parsed.model_dump_json(indent=2).encode("utf-8")
            parsed_path = os.path.join(paper_dir, "parsed_document.json")
            atomic_write_unique(json_bytes, parsed_path)

            store.record_artifact(
                task_id,
                paper["id"],
                "parsed_document",
                "json",
                parsed_path,
            )
            store.update_paper_status(paper["id"], "parsed")
            parsed_count += 1
        except Exception as exc:
            store.update_paper_status(
                paper["id"], "degraded", error=f"parse failed: {exc}"
            )
            degraded_count += 1

    # Advance to EXTRACTING (fast-path: READING stage is not yet implemented)
    store.advance(task_id, "worker", "EXTRACTING")
    return {"parsed": parsed_count, "degraded": degraded_count}
