"""Worker entry point -- wires mock adapters to stage handlers and starts the Worker."""

import asyncio
import logging

from workflow_engine import WorkflowStore
from workflow_worker import WorkflowWorker, StageRegistry, FunctionStageHandler
from workflow_config import WorkflowConfig

from adapters.mock_search import MockSearchProvider, MockFulltextProvider
from adapters.mock_embedding import MockEmbeddingRetriever, MockReranker
from adapters.mock_parser import MockDocumentParser
from pipeline.search_stage import run_search_stage
from pipeline.screening_stage import run_screening_stage
from pipeline.fulltext_stage import run_fulltext_stage
from pipeline.parse_stage import run_parse_stage
from pipeline.reading_stage import run_reading_stage
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

    if config.agent_backend == "pi":
        from adapters.pi_agent import PiAgentAdapter
        agent = PiAgentAdapter(
            pi_command=config.pi_command,
            timeout=config.pi_timeout,
        )
        logger.info("Using PiAgentAdapter (real LLM)")
    else:
        from adapters.mock_agent import MockAgentAdapter
        agent = MockAgentAdapter()
        logger.info("Using MockAgentAdapter (mock)")

    # Wire stage handlers
    registry = StageRegistry(config)
    registry.register("SEARCHING", FunctionStageHandler("SEARCHING", lambda ctx, job, store: run_search_stage(ctx, job, store, search)))
    registry.register("SCREENING", FunctionStageHandler("SCREENING", lambda ctx, job, store: run_screening_stage(ctx, job, store, retriever, reranker, agent)))
    registry.register("FETCHING_FULLTEXT", FunctionStageHandler("FETCHING_FULLTEXT", lambda ctx, job, store: run_fulltext_stage(ctx, job, store, fulltext)))
    registry.register("PARSING", FunctionStageHandler("PARSING", lambda ctx, job, store: run_parse_stage(ctx, job, store, parser)))
    registry.register("READING", FunctionStageHandler("READING", lambda ctx, job, store: run_reading_stage(ctx, job, store)))
    registry.register("EXTRACTING", FunctionStageHandler("EXTRACTING", lambda ctx, job, store: run_extraction_stage(ctx, job, store, agent)))
    registry.register("VALIDATING", FunctionStageHandler("VALIDATING", lambda ctx, job, store: run_validation_stage(ctx, job, store)))
    registry.register("GENERATING_REPORT", FunctionStageHandler("GENERATING_REPORT", lambda ctx, job, store: run_report_stage(ctx, job, store, agent)))
    registry.validate_required_stages()

    worker = WorkflowWorker(config, store, registry)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
