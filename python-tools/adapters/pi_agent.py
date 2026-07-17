"""PiAgentAdapter -- implements AgentAdapter protocol via Pi RPC."""

import json
import logging
import os
import re
from pathlib import Path

from workflow_models import (
    TaskDefinition, PaperMetadata, ParsedDocument,
    SampleExtraction, ResearchReport, ReportSection,
    ScreeningDecision, EvidenceLocator, CompositionRatio,
    ProcessStep, PerformanceMetric, MaterialComponent,
    TestCondition,
)
from pi_workflow_client import PiWorkflowClient

logger = logging.getLogger(__name__)


class PiAgentAdapter:
    """Calls a local Pi RPC subprocess for literature workflow tasks."""

    def __init__(
        self,
        pi_command: str | None = None,
        timeout: int = 300,
        prompts_dir: str | None = None,
    ):
        self.pi_command = pi_command or os.environ.get("PI_COMMAND", "pi")
        self.timeout = int(os.environ.get("PI_TIMEOUT_SECONDS", str(timeout)))
        self.prompts_dir = Path(prompts_dir or os.path.join(
            os.path.dirname(__file__), "..", "prompts"
        ))
        self._client: PiWorkflowClient | None = None

    async def _get_client(self) -> PiWorkflowClient:
        if self._client is None:
            self._client = PiWorkflowClient(
                pi_command=self.pi_command,
                timeout=self.timeout,
            )
            await self._client.start()
        return self._client

    def _load_prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _extract_json(self, text: str) -> dict | list:
        """Extract JSON from Pi's response. Tries markdown code blocks first,
        then raw JSON."""
        # Try ```json ... ``` block
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        # Try raw JSON
        text = text.strip()
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        raise ValueError(f"Cannot extract JSON from response: {text[:200]}...")

    async def screen_abstracts(
        self, task: TaskDefinition, papers: list[PaperMetadata]
    ) -> list[ScreeningDecision]:
        client = await self._get_client()
        template = self._load_prompt("screen_abstracts")

        papers_text = ""
        for i, p in enumerate(papers):
            papers_text += (
                f"### Paper {i+1}\n"
                f"Work ID: {p.work_id}\n"
                f"Title: {p.title}\n"
                f"Authors: {', '.join(p.authors)}\n"
                f"Year: {p.year}\n"
                f"Abstract: {p.abstract}\n\n"
            )

        prompt = template.replace("{{TASK_DEFINITION}}", task.model_dump_json(indent=2))
        prompt = prompt.replace("{{PAPERS}}", papers_text)

        response = await client.send_prompt(prompt)
        data = self._extract_json(response)

        decisions = []
        for item in data:
            paper = next((p for p in papers if p.work_id == item["work_id"]), None)
            if paper:
                decisions.append(ScreeningDecision(
                    paper=paper,
                    include=item.get("include", True),
                    role_tags=item.get("role_tags", []),
                    reason=item.get("reason", ""),
                ))
        return decisions

    async def extract_paper(
        self, task: TaskDefinition, parsed: ParsedDocument
    ) -> list[SampleExtraction]:
        client = await self._get_client()
        template = self._load_prompt("extract_paper")

        # Build text from parsed pages (limit to avoid context overflow)
        full_text = ""
        for page in parsed.pages:
            full_text += f"\n--- Page {page.page_number} ---\n"
            for block in page.blocks:
                full_text += block.text + "\n"
        # Truncate if too long
        if len(full_text) > 30000:
            full_text = full_text[:30000] + "\n... (truncated)"

        prompt = template.replace("{{TASK_DEFINITION}}", task.model_dump_json(indent=2))
        prompt = prompt.replace("{{FULL_TEXT}}", full_text)

        response = await client.send_prompt(prompt)
        data = self._extract_json(response)

        samples = []
        for s_data in (data if isinstance(data, list) else data.get("samples", [])):
            evidence = [
                EvidenceLocator(**e) for e in s_data.get("evidence", [])
            ]
            samples.append(SampleExtraction(
                sample_id=s_data.get("sample_id", "S1"),
                components=[MaterialComponent(**c) for c in s_data.get("components", [])],
                ratios=[CompositionRatio(**r) for r in s_data.get("ratios", [])],
                process_steps=[ProcessStep(**ps) for ps in s_data.get("process_steps", [])],
                test_conditions=[TestCondition(**tc) for tc in s_data.get("test_conditions", [])],
                performance_metrics=[PerformanceMetric(**pm) for pm in s_data.get("performance_metrics", [])],
                evidence=evidence,
                is_abstract_only=False,
            ))
        return samples

    async def generate_report(
        self, task: TaskDefinition, extractions: list[SampleExtraction]
    ) -> ResearchReport:
        client = await self._get_client()
        template = self._load_prompt("generate_report")

        extractions_text = ""
        for i, ext in enumerate(extractions):
            extractions_text += f"### Extraction {i+1}\n"
            extractions_text += ext.model_dump_json(indent=2) + "\n\n"
        if len(extractions_text) > 30000:
            extractions_text = extractions_text[:30000] + "\n... (truncated)"

        prompt = template.replace("{{TASK_DEFINITION}}", task.model_dump_json(indent=2))
        prompt = prompt.replace("{{EXTRACTIONS}}", extractions_text)

        response = await client.send_prompt(prompt)
        data = self._extract_json(response)

        sections = []
        for s_data in (data if isinstance(data, list) else data.get("sections", [])):
            sections.append(ReportSection(
                heading=s_data["heading"],
                content=s_data["content"],
                order=s_data.get("order", len(sections) + 1),
            ))
        return ResearchReport(task_id="", version=1, sections=sections)

    async def close(self):
        if self._client:
            await self._client.stop()
            self._client = None
