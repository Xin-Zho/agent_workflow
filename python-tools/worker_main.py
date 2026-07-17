"""Worker entry point -- wires mock adapters to stage handlers and starts the Worker."""

import asyncio
import logging

from workflow_engine import WorkflowStore
from workflow_worker import WorkflowWorker, StageRegistry
from workflow_config import WorkflowConfig

from adapters.mock_search import MockSearchProvider, MockFulltextProvider
from adapters.mock_embedding import MockEmbeddingRetriever, MockReranker
from adapters.mock_parser import MockDocumentParser
from adapters.mock_agent import MockAgentAdapter

from pipeline.search_stage import run_search_stage
from pipeline.screening_stage import run_screening_stage
from pipeline.fulltext_stage import run_fulltext_stage
from pipeline.parse_stage import run_parse_stage
from pipeline.extraction_stage import run_extraction_stage
from pipeline.validation_stage import run_validation_stage
from pipeline.report_stage import run_report_stage


async def main():
    logging.basicConfig(level=logging.INFO)
    config = WorkflowConfig()
    store = WorkflowStore(config.db_path)

    # Create adapters
    search = MockSearchProvider()
    fulltext = MockFulltextProvider()
    retriever = MockEmbeddingRetriever()
    reranker = MockReranker()
    parser = MockDocumentParser()
    agent = MockAgentAdapter()

    # Wire stage handlers
    registry = StageRegistry(config)

    async def search_handler(job, s):
        return await run_search_stage(job, s, search)
    registry.register("SEARCHING", type("Handler", (), {"run": search_handler})())

    async def screening_handler(job, s):
        return await run_screening_stage(job, s, retriever, reranker, agent)
    registry.register("SCREENING", type("Handler", (), {"run": screening_handler})())

    async def fulltext_handler(job, s):
        return await run_fulltext_stage(job, s, fulltext)
    registry.register("FETCHING_FULLTEXT", type("Handler", (), {"run": fulltext_handler})())

    async def parse_handler(job, s):
        return await run_parse_stage(job, s, parser)
    registry.register("PARSING", type("Handler", (), {"run": parse_handler})())

    async def extraction_handler(job, s):
        return await run_extraction_stage(job, s, agent)
    registry.register("EXTRACTING", type("Handler", (), {"run": extraction_handler})())

    async def validation_handler(job, s):
        return await run_validation_stage(job, s)
    registry.register("VALIDATING", type("Handler", (), {"run": validation_handler})())

    async def report_handler(job, s):
        return await run_report_stage(job, s, agent)
    registry.register("GENERATING_REPORT", type("Handler", (), {"run": report_handler})())

    worker = WorkflowWorker(config, store, registry)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
