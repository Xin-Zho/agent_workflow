"""VALIDATING stage: validate extraction data integrity.

Phase 2A minimal validation:
  1. Every evidence_id in composition ratios, process steps, test
     conditions, and performance metrics references a valid evidence
     entry within the same extraction.
  2. source_type on each EvidenceLocator is one of the allowed literals.
  3. Page numbers are positive integers.
"""

from workflow_engine import WorkerContext, WorkflowStore
from workflow_models import (
    SampleExtraction,
    EvidenceLocator,
)


VALID_SOURCE_TYPES = {"explicit", "derived", "estimated", "inferred", "missing"}


async def run_validation_stage(
    ctx: WorkerContext,
    job: dict,
    store: WorkflowStore,
) -> dict:
    """Execute the VALIDATING pipeline stage.

    Validates all extractions for the current task. Returns a dict with
    pass/fail counts and a list of issues found. Advances to
    GENERATING_REPORT on success (issues are warnings, not blockers).
    """
    task_id = job["task_id"]
    extractions = store.list_extractions_for_worker(ctx)

    issues: list[dict] = []
    validated_count = 0

    for ext in extractions:
        ext_id = ext["id"]
        paper_id = ext["paper_id"]
        payload = ext.get("payload", {})
        if not payload:
            issues.append({
                "extraction_id": ext_id,
                "paper_id": paper_id,
                "field": "payload",
                "message": "Empty extraction payload",
            })
            continue

        try:
            sample = SampleExtraction(**payload)
        except Exception as exc:
            issues.append({
                "extraction_id": ext_id,
                "paper_id": paper_id,
                "field": "payload",
                "message": f"Invalid SampleExtraction: {exc}",
            })
            continue

        # Collect all declared evidence IDs for this extraction
        declared_ids = {e.evidence_id for e in sample.evidence}

        # 1. Validate evidence_ids references
        for comp_ratio in sample.ratios:
            for eid in comp_ratio.evidence_ids:
                if eid not in declared_ids:
                    issues.append({
                        "extraction_id": ext_id,
                        "paper_id": paper_id,
                        "field": f"ratios/{comp_ratio.component}/evidence_ids",
                        "message": f"evidence_id '{eid}' not found in extraction evidence list",
                    })

        for step in sample.process_steps:
            for eid in step.evidence_ids:
                if eid not in declared_ids:
                    issues.append({
                        "extraction_id": ext_id,
                        "paper_id": paper_id,
                        "field": f"process_steps/step{step.step_number}/evidence_ids",
                        "message": f"evidence_id '{eid}' not found in extraction evidence list",
                    })

        for tc in sample.test_conditions:
            for eid in tc.evidence_ids:
                if eid not in declared_ids:
                    issues.append({
                        "extraction_id": ext_id,
                        "paper_id": paper_id,
                        "field": f"test_conditions/{tc.property}/evidence_ids",
                        "message": f"evidence_id '{eid}' not found in extraction evidence list",
                    })

        for pm in sample.performance_metrics:
            for eid in pm.evidence_ids:
                if eid not in declared_ids:
                    issues.append({
                        "extraction_id": ext_id,
                        "paper_id": paper_id,
                        "field": f"performance_metrics/{pm.property}/evidence_ids",
                        "message": f"evidence_id '{eid}' not found in extraction evidence list",
                    })

        # 2. Validate source_type
        for ev in sample.evidence:
            if ev.source_type not in VALID_SOURCE_TYPES:
                issues.append({
                    "extraction_id": ext_id,
                    "paper_id": paper_id,
                    "field": f"evidence/{ev.evidence_id}/source_type",
                    "message": (
                        f"Invalid source_type '{ev.source_type}'; "
                        f"must be one of {sorted(VALID_SOURCE_TYPES)}"
                    ),
                })

        # 3. Validate page numbers are positive
        for ev in sample.evidence:
            if ev.page < 1:
                issues.append({
                    "extraction_id": ext_id,
                    "paper_id": paper_id,
                    "field": f"evidence/{ev.evidence_id}/page",
                    "message": f"Page number {ev.page} must be >= 1",
                })

        validated_count += 1

    # Advance to GENERATING_REPORT (issues are non-blocking)
    store.advance_for_worker(ctx, "GENERATING_REPORT")

    return {
        "validated_count": validated_count,
        "total_extractions": len(extractions),
        "issues_count": len(issues),
        "issues": issues,
    }
