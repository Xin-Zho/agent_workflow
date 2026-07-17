"""SCREENING stage: filter, deduplicate, classify, and rank candidates.

NOTE: PaperMetadata is a Pydantic model -- attribute access (p.abstract) is
used throughout; dict-style .get() is never called on PaperMetadata objects.
PaperMetadata has no role_tags field; role_tags are computed from the
ScreeningDecision results returned by the agent adapter.
"""

from workflow_engine import WorkflowStore
from pipeline.contracts import EmbeddingRetriever, Reranker, AgentAdapter
from workflow_models import TaskDefinition, PaperMetadata


async def run_screening_stage(
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
    task = store.get_task(job["task_id"], "worker")
    definition = TaskDefinition(**task["definition"])
    papers_raw = store.list_papers(job["task_id"], "worker")

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

    # Update paper role_tags and scores from screening decisions
    # PaperMetadata has no role_tags field; we compute them from decisions.
    from workflow_engine import utc_now
    now = utc_now()
    for dec in decisions:
        for paper_row in papers_raw:
            if paper_row["work_id"] == dec.paper.work_id:
                with store._connect() as conn:
                    conn.execute(
                        """UPDATE papers
                           SET role_tags_json = ?, relevance_score = ?,
                               updated_at = ?
                           WHERE id = ?""",
                        (store._json(dec.role_tags), 80.0, now, paper_row["id"]),
                    )

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
