"""EXTRACTING stage: extract structured data from parsed documents."""

import json
import os

from workflow_engine import WorkflowStore
from pipeline.contracts import AgentAdapter
from workflow_models import TaskDefinition, ParsedDocument, SampleExtraction


async def run_extraction_stage(
    job: dict,
    store: WorkflowStore,
    agent: AgentAdapter,
) -> dict:
    """Execute the EXTRACTING pipeline stage.

    For each parsed paper, loads the ParsedDocument from its JSON artifact,
    delegates to AgentAdapter.extract_paper(), and records each
    SampleExtraction via store.record_extraction(). Advances the task to
    VALIDATING when complete.
    """
    task_id = job["task_id"]
    task = store.get_task(task_id, "worker")
    definition = TaskDefinition(**task["definition"])

    papers = store.list_papers(task_id, "worker")
    parsed_papers = [p for p in papers if p.get("paper_status") == "parsed"]

    if not parsed_papers:
        store.advance(task_id, "worker", "VALIDATING")
        return {"extracted": 0}

    # Retrieve cached parsed_document artifacts
    doc_artifacts = store.get_artifacts(task_id, "parsed_document")
    doc_by_paper = {a["paper_id"]: a for a in doc_artifacts}

    extracted_count = 0
    for paper in parsed_papers:
        doc_artifact = doc_by_paper.get(paper["id"])
        if doc_artifact is None or not os.path.exists(doc_artifact["path"]):
            store.update_paper_status(
                paper["id"], "degraded", error="parsed document not found"
            )
            continue

        try:
            with open(doc_artifact["path"], "r", encoding="utf-8") as f:
                parsed_doc = ParsedDocument(**json.load(f))

            extractions: list[SampleExtraction] = await agent.extract_paper(
                definition, parsed_doc
            )

            for sample_extraction in extractions:
                payload = sample_extraction.model_dump()
                store.record_extraction(
                    task_id=task_id,
                    actor_id="worker",
                    paper_id=paper["id"],
                    payload=payload,
                    source_type="explicit",
                    confidence_score=80.0,
                )

            store.update_paper_status(paper["id"], "extracted")
            extracted_count += 1
        except Exception as exc:
            store.update_paper_status(
                paper["id"], "degraded", error=f"extraction failed: {exc}"
            )

    # Advance to VALIDATING
    store.advance(task_id, "worker", "VALIDATING")
    return {"extracted": extracted_count}
