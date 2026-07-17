"""GENERATING_REPORT stage: synthesize findings into a research report."""

import json
import os

from workflow_engine import WorkerContext, WorkflowStore
from artifact_utils import atomic_write_unique
from pipeline.contracts import AgentAdapter
from workflow_models import TaskDefinition, SampleExtraction


async def run_report_stage(
    ctx: WorkerContext,
    job: dict,
    store: WorkflowStore,
    agent: AgentAdapter,
) -> dict:
    """Execute the GENERATING_REPORT pipeline stage.

    Loads all current extractions, delegates to AgentAdapter.generate_report()
    to produce a structured ResearchReport, renders it as Markdown, persists
    it via atomic_write_unique, records the report, and transitions to
    WAITING_DATA_REVIEW.
    """
    task_id = job["task_id"]
    task = store.get_task_for_worker(ctx)
    definition = TaskDefinition(**task["definition"])

    # Load all current extractions
    extraction_rows = store.list_extractions_for_worker(ctx)
    extractions: list[SampleExtraction] = []
    for ext in extraction_rows:
        payload = ext.get("payload", {})
        if payload:
            try:
                extractions.append(SampleExtraction(**payload))
            except Exception:
                pass  # skip malformed extractions

    report = await agent.generate_report(definition, extractions)

    # Render markdown from the ResearchReport sections
    md_content = _render_report(report)

    # Write report to disk
    task_dir = os.path.join(
        os.path.dirname(store.db_path),
        "..",
        "data",
        "tasks",
        task_id,
    )
    os.makedirs(task_dir, exist_ok=True)
    report_path = os.path.join(task_dir, f"report_v{report.version}.md")
    atomic_write_unique(md_content.encode("utf-8"), report_path)

    # Record the report in the store
    store.record_report(task_id, report_path, format="markdown")

    # Transition to WAITING_DATA_REVIEW (human review gate)
    store.request_data_review_for_worker(ctx)

    return {
        "report_path": report_path,
        "version": report.version,
        "sections": len(report.sections),
    }


def _render_report(report) -> str:
    """Render a ResearchReport into a Markdown string."""
    lines = []
    for section in sorted(report.sections, key=lambda s: s.order):
        lines.append(f"## {section.heading}")
        lines.append("")
        lines.append(section.content)
        lines.append("")
    return "\n".join(lines)
