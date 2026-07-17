"""READING stage: analyze parsed documents and produce a reading plan.

Tags blocks by semantic type (table, figure_caption, formula, process,
performance) and generates a reading_plan artifact that identifies which
blocks to focus on during the EXTRACTING stage.
"""

import hashlib
import json
import os

from artifact_utils import atomic_write_unique
from workflow_engine import WorkerContext, WorkflowStore
from workflow_models import ParsedDocument, ParsedBlock

# Keywords used to tag blocks by material-science content type.
# Block type from the parser (block_type field) is checked first;
# text content keywords serve as a fallback for untagged blocks.
_TAG_KEYWORDS: dict[str, list[str]] = {
    "formula": [
        "formula", "equation", "eq.", "formulation",
        "chemical formula", "molecular formula",
        "化学式", "分子式", "公式", "方程",
    ],
    "process": [
        "synthesis", "preparation", "fabrication", "deposition",
        "annealing", "sintering", "calcination", "spin-coating",
        "casting", "mixing", "stirring", "drying", "curing",
        "polymerization", "electrospinning", "solution", "dispersion",
        "dissolve", "dissolution", "thermal treatment", "heat treatment",
        "hydrothermal", "sol-gel", "coprecipitation", "precipitation",
        "ball milling", "mechanical alloying", " melt spinning",
        "合成", "制备", "制造", "沉积", "退火", "烧结", "煅烧", "混合",
    ],
    "performance": [
        "conductivity", "capacitance", "voltage", "current density",
        "efficiency", "stability", "strength", "modulus", "toughness",
        "permeability", "selectivity", "sensitivity", "response time",
        "cycle life", "capacity retention", "ionic conductivity",
        "power density", "energy density", "performance",
        "electrochemical", "mechanical property",
        "导电率", "电容", "电压", "效率", "性能", "稳定性",
    ],
}


def _tag_block(block: ParsedBlock) -> str | None:
    """Return the semantic tag for a block, or None if no tag applies.

    Priority: explicit parser block_type -> keyword match on text.
    """
    block_type_lower = block.block_type.lower().replace(" ", "_")

    # Parser-level tags map directly
    if block_type_lower in ("table", "figure_caption"):
        return block_type_lower

    # Keyword matching for untyped blocks
    text_lower = block.text.lower()
    for tag, keywords in _TAG_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return tag

    return None


def _build_reading_plan(parsed_doc: ParsedDocument) -> dict:
    """Build a reading plan from a parsed document.

    Returns a dict with:
      - block_groups: mapping of tag -> list of block_ids
      - focus_block_ids: prioritized list of block IDs for extraction
    """
    block_groups: dict[str, list[str]] = {
        "table": [],
        "figure_caption": [],
        "formula": [],
        "process": [],
        "performance": [],
        "other": [],
    }

    for page in parsed_doc.pages:
        for block in page.blocks:
            tag = _tag_block(block)
            if tag:
                block_groups[tag].append(block.block_id)
            else:
                block_groups["other"].append(block.block_id)

    # Focus blocks ordered by extraction priority: tables first, then
    # performance, process, formulas, figure captions, then other.
    focus_order = ["table", "performance", "process", "formula",
                   "figure_caption"]
    focus_block_ids: list[str] = []
    seen: set[str] = set()
    for tag in focus_order:
        for bid in block_groups[tag]:
            if bid not in seen:
                seen.add(bid)
                focus_block_ids.append(bid)

    return {
        "block_groups": block_groups,
        "focus_block_ids": focus_block_ids,
    }


async def run_reading_stage(
    ctx: WorkerContext,
    job: dict,
    store: WorkflowStore,
) -> dict:
    """Execute the READING pipeline stage.

    For each parsed paper, loads the ParsedDocument from its JSON artifact,
    tags blocks by content type, and generates a reading_plan artifact that
    identifies which blocks to focus on during extraction. Advances the task
    to EXTRACTING when complete.

    Idempotency: on retry, already-analyzed papers are detected via existing
    reading_plan artifacts and skipped. Content-addressed naming prevents
    duplicate artifacts.
    """
    papers = store.list_papers_for_worker(ctx)
    parsed_papers = [p for p in papers if p.get("paper_status") == "parsed"]

    if not parsed_papers:
        store.advance_for_worker(ctx, "EXTRACTING")
        return {"analyzed": 0}

    # Retrieve parsed_document artifacts
    task_id = job["task_id"]
    doc_artifacts = store.get_artifacts(task_id, "parsed_document")
    doc_by_paper = {a["paper_id"]: a for a in doc_artifacts}

    # Retrieve existing reading plans for retry idempotency
    existing_plans = store.get_artifacts(task_id, "reading_plan")
    already_planned = {a["paper_id"] for a in existing_plans}

    analyzed_count = 0
    for paper in parsed_papers:
        if paper["id"] in already_planned:
            analyzed_count += 1
            continue

        doc_artifact = doc_by_paper.get(paper["id"])
        if doc_artifact is None or not os.path.exists(doc_artifact["path"]):
            store.update_paper_status(
                paper["id"], "degraded", error="parsed document not found"
            )
            continue

        try:
            with open(doc_artifact["path"], "r", encoding="utf-8") as f:
                parsed_doc = ParsedDocument(**json.load(f))

            # Build reading plan from parsed content
            reading_plan = _build_reading_plan(parsed_doc)

            # Write reading plan as a content-addressed artifact
            paper_dir = os.path.dirname(doc_artifact["path"])
            os.makedirs(paper_dir, exist_ok=True)

            plan_bytes = json.dumps(
                reading_plan, ensure_ascii=False, indent=2
            ).encode("utf-8")
            sha256 = hashlib.sha256(plan_bytes).hexdigest()
            plan_path = os.path.join(
                paper_dir, f"reading_plan_{sha256[:16]}.json"
            )
            atomic_write_unique(plan_bytes, plan_path, expected_sha256=sha256)

            store.record_artifact(
                task_id,
                paper["id"],
                "reading_plan",
                "json",
                plan_path,
                sha256,
            )
            analyzed_count += 1
        except FileExistsError:
            # Race -- another worker already wrote identical content
            analyzed_count += 1
        except Exception as exc:
            store.update_paper_status(
                paper["id"], "degraded", error=f"reading stage failed: {exc}"
            )

    # Advance to EXTRACTING
    store.advance_for_worker(ctx, "EXTRACTING")
    return {"analyzed": analyzed_count}
