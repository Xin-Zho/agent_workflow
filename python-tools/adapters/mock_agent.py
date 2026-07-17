"""Mock agent adapter returning fixed extraction results with mixed evidence levels."""

import json
from pathlib import Path

from workflow_models import (
    TaskDefinition, ParsedDocument, SampleExtraction, ResearchReport,
    ReportSection, PaperMetadata, ScreeningDecision,
    EvidenceLocator, CompositionRatio, ProcessStep, PerformanceMetric,
    MaterialComponent,
)


class MockAgentAdapter:
    def __init__(self, fixture_path: str | None = None):
        if fixture_path is None:
            fixture_path = str(
                Path(__file__).resolve().parent.parent / "fixtures" / "mock_extraction.json"
            )
        with open(fixture_path, encoding="utf-8") as f:
            self._fixture = json.load(f)

    async def screen_abstracts(self, task: TaskDefinition,
                               papers: list[PaperMetadata]) -> list[ScreeningDecision]:
        decisions = []
        for p in papers:
            include = bool(p.abstract) and p.document_type != "retracted"
            decisions.append(ScreeningDecision(
                paper=p,
                include=include,
                role_tags=p.get("role_tags", []),
                reason="mock screening",
            ))
        return decisions

    async def extract_paper(self, task: TaskDefinition,
                            parsed: ParsedDocument) -> list[SampleExtraction]:
        samples = []
        for s in self._fixture["samples"]:
            evidence = [
                EvidenceLocator(**e) for e in s.get("evidence", [])
            ]
            samples.append(SampleExtraction(
                sample_id=s["sample_id"],
                components=[MaterialComponent(**c) for c in s.get("components", [])],
                ratios=[CompositionRatio(**r) for r in s.get("ratios", [])],
                process_steps=[ProcessStep(**ps) for ps in s.get("process_steps", [])],
                performance_metrics=[PerformanceMetric(**pm) for pm in s.get("performance_metrics", [])],
                evidence=evidence,
                is_abstract_only=False,
            ))
        return samples

    async def generate_report(self, task: TaskDefinition,
                              extractions: list[SampleExtraction]) -> ResearchReport:
        sections = [
            ReportSection(heading="研究目标和约束", content=f"研究对象: {task.research_object}", order=1),
            ReportSection(heading="候选论文列表", content="Mock papers (see task)", order=2),
            ReportSection(heading="目标性能", content=str(task.target_metrics), order=3),
            ReportSection(heading="候选结构", content="PEO/AgNW composite", order=4),
            ReportSection(heading="实验室可行工艺体系", content="Solution casting", order=5),
            ReportSection(heading="成分和配比", content=f"Extractions: {len(extractions)} papers", order=6),
            ReportSection(heading="配方—工艺—性能表", content="See attached data", order=7),
            ReportSection(heading="数据缺失和冲突提示", content="All mock data — low confidence", order=8),
            ReportSection(heading="下一批实验点", content="TBD", order=9),
            ReportSection(heading="引用列表", content="[1] Mock et al. (2024)", order=10),
        ]
        return ResearchReport(task_id="", version=1, sections=sections)
