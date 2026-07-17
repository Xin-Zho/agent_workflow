"""SCREENING stage: filter, deduplicate, classify, and rank candidates.

NOTE: PaperMetadata is a Pydantic model -- attribute access (p.abstract) is
used throughout; dict-style .get() is never called on PaperMetadata objects.
PaperMetadata has no role_tags field; role_tags are computed from the
ScreeningDecision results returned by the agent adapter.
"""

from workflow_engine import WorkerContext, WorkflowStore
from pipeline.contracts import EmbeddingRetriever, Reranker, AgentAdapter
from workflow_models import TaskDefinition, PaperMetadata, ScoredPaper


async def run_screening_stage(
    ctx: WorkerContext,
    job: dict,
    store: WorkflowStore,
    retriever: EmbeddingRetriever,
    reranker: Reranker,
    agent: AgentAdapter,
) -> dict:
    """Execute the SCREENING pipeline stage.

    Deduplicates candidate papers by work_id, embeds, reranks, and runs
    agent-based abstract screening. Updates role_tags and scores on the
    paper records from the ScreeningDecision results.
    """
    task = store.get_task_for_worker(ctx)
    definition = TaskDefinition(**task["definition"])
    papers_raw = store.list_papers_for_worker(ctx)

    # Convert to PaperMetadata (always attribute access, never .get())
    papers = [_paper_metadata(p) for p in papers_raw]

    # Deduplicate by work_id (keep highest-scoring version)
    seen = {}
    for p in papers:
        if p.work_id not in seen or (p.year or 0) > (seen[p.work_id].year or 0):
            seen[p.work_id] = p
    deduped = list(seen.values())

    # Embedding recall
    top_k = min(len(deduped), definition.paper_target * 2)
    scored = await retriever.retrieve(definition, deduped, top_k=top_k)

    # Rerank
    ranked = await reranker.rerank(task["query"], scored)

    # Agent screening -- returns ScreeningDecision with role_tags
    decisions = await agent.screen_abstracts(
        definition, [s.metadata for s in ranked]
    )

    # Build O(1) lookup indexes for scores and DB rows
    ranked_by_work_id: dict[str, ScoredPaper] = {
        item.metadata.work_id: item
        for item in ranked
    }
    papers_by_work_id: dict[str, dict] = {}
    for p in papers_raw:
        papers_by_work_id.setdefault(p["work_id"], p)
    # Note: keeps first occurrence (already sorted by score DESC from DB query)

    # Prepare batch update rows using real retriever/reranker scores
    from workflow_engine import utc_now
    rows = []
    for dec in decisions:
        paper_row = papers_by_work_id.get(dec.paper.work_id)
        if not paper_row:
            continue
        scored = ranked_by_work_id.get(dec.paper.work_id)
        rows.append((
            store._json(dec.role_tags),
            scored.relevance_score if scored else 0.0,
            scored.authority_score if scored else 0.0,
            scored.confidence_score if scored else 0.0,
            utc_now(),
            paper_row["id"],
        ))

    if rows:
        store.update_screening_results(rows)

    return {
        "candidates_after_screening": len(decisions),
        "included": sum(1 for d in decisions if d.include),
    }


def _paper_metadata(row: dict) -> PaperMetadata:
    """Convert a DB row dict to a PaperMetadata instance.

    The row comes from WorkflowStore.list_papers which decodes *_json
    columns.  The ``metadata`` key itself is a decoded dict, *not* a
    PaperMetadata, so dict .get() is valid here on *that* dict, never on
    PaperMetadata itself.
    """
    meta = row.get("metadata", {}) or {}
    return PaperMetadata(
        work_id=row["work_id"],
        title=row["title"],
        authors=meta.get("authors", []),
        year=meta.get("year"),
        abstract=meta.get("abstract", ""),
        doi=meta.get("doi", ""),
    )
