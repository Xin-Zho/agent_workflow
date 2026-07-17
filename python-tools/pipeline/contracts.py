"""Protocol interfaces for pipeline stages. All adapters (mock or real)
must satisfy these protocols. Stage handlers depend on these, never on
concrete adapter implementations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from workflow_models import (
    PaperMetadata,
    ParsedDocument,
    ResearchReport,
    SampleExtraction,
    ScoredPaper,
    ScreeningDecision,
    SearchQuery,
    TaskDefinition,
)


@runtime_checkable
class SearchProvider(Protocol):
    async def search(self, query: SearchQuery) -> list[PaperMetadata]: ...


@runtime_checkable
class EmbeddingRetriever(Protocol):
    async def retrieve(
        self, task: TaskDefinition, papers: list[PaperMetadata], top_k: int
    ) -> list[ScoredPaper]: ...


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, papers: list[ScoredPaper]
    ) -> list[ScoredPaper]: ...


@runtime_checkable
class FulltextProvider(Protocol):
    async def fetch(self, paper: PaperMetadata) -> bytes | None: ...


@runtime_checkable
class DocumentParser(Protocol):
    async def parse(self, pdf_bytes: bytes) -> ParsedDocument: ...


@runtime_checkable
class AgentAdapter(Protocol):
    async def screen_abstracts(
        self, task: TaskDefinition, papers: list[PaperMetadata]
    ) -> list[ScreeningDecision]: ...
    async def extract_paper(
        self, task: TaskDefinition, parsed: ParsedDocument
    ) -> list[SampleExtraction]: ...
    async def generate_report(
        self, task: TaskDefinition, extractions: list[SampleExtraction]
    ) -> ResearchReport: ...
