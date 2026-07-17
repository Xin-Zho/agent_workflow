from workflow_models import TaskDefinition, PaperMetadata, ScoredPaper


class MockEmbeddingRetriever:
    async def retrieve(self, task: TaskDefinition, papers: list[PaperMetadata],
                       top_k: int) -> list[ScoredPaper]:
        scored = []
        for i, p in enumerate(papers):
            score = 100.0 - i * 5.0
            scored.append(ScoredPaper(metadata=p, relevance_score=max(score, 10.0)))
        return sorted(scored, key=lambda s: s.relevance_score, reverse=True)[:top_k]


class MockReranker:
    async def rerank(self, query: str, papers: list[ScoredPaper]) -> list[ScoredPaper]:
        return sorted(papers, key=lambda s: s.relevance_score, reverse=True)
