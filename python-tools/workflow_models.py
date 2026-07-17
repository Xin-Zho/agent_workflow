"""Unified Pydantic data models for the material-science literature workflow.

Design invariants:
- source_type is per-field (EvidenceLocator), NOT per-extraction.
- Every data-carrying field (ratio, process step, performance, test condition)
  references its evidence via evidence_ids.
- Pydantic list/dict fields use Field(default_factory=...).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PaperStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    FETCHED = "fetched"
    PARSED = "parsed"
    EXTRACTED = "extracted"
    DEGRADED = "degraded"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class RatioBasis(StrEnum):
    MASS_FRACTION = "mass_fraction"
    VOLUME_FRACTION = "volume_fraction"
    MOLE_FRACTION = "mole_fraction"
    MASS_PARTS = "mass_parts"
    RELATIVE_TO_MATRIX = "relative_to_matrix"
    RELATIVE_TO_TOTAL = "relative_to_total"
    RELATIVE_TO_PRECURSOR = "relative_to_precursor"
    UNSPECIFIED = "unspecified"


class VersionType(StrEnum):
    PREPRINT = "preprint"
    JOURNAL = "journal"
    CORRIGENDUM = "corrigendum"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

class Metric(BaseModel):
    name: str
    unit: str | None = None
    target_range: str | None = None


class TaskDefinition(BaseModel):
    research_object: str
    application: str
    target_metrics: list[Metric] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    optimization_objectives: list[str] = Field(default_factory=list)
    acceptable_tradeoffs: list[str] = Field(default_factory=list)
    paper_target: int = Field(default=10, ge=5, le=200)
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    temporary_lab_constraints: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence (per-field source tracking)
# ---------------------------------------------------------------------------

class EvidenceLocator(BaseModel):
    evidence_id: str
    field_path: str  # e.g. "samples/S1/ratios/2/raw_value"
    work_id: str
    file_version: str
    page: int = Field(ge=1)
    section: str | None = None
    figure: str | None = None
    table: str | None = None
    quote_or_value: str | None = None
    source_type: Literal["explicit", "derived", "estimated", "inferred", "missing"]


# ---------------------------------------------------------------------------
# Material extraction fields (each carries evidence_ids)
# ---------------------------------------------------------------------------

class MaterialComponent(BaseModel):
    name: str
    role: str | None = None
    supplier: str | None = None


class CompositionRatio(BaseModel):
    component: str
    raw_value: str
    raw_unit: str
    ratio_basis: RatioBasis = RatioBasis.UNSPECIFIED
    normalized_value: float | None = None
    normalized_unit: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ProcessStep(BaseModel):
    step_number: int
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    equipment: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TestCondition(BaseModel):
    property: str
    method: str | None = None
    standard: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class PerformanceMetric(BaseModel):
    property: str
    value: str
    unit: str
    test_condition: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregated extraction
# ---------------------------------------------------------------------------

class SampleExtraction(BaseModel):
    sample_id: str
    components: list[MaterialComponent] = Field(default_factory=list)
    ratios: list[CompositionRatio] = Field(default_factory=list)
    process_steps: list[ProcessStep] = Field(default_factory=list)
    test_conditions: list[TestCondition] = Field(default_factory=list)
    performance_metrics: list[PerformanceMetric] = Field(default_factory=list)
    evidence: list[EvidenceLocator] = Field(default_factory=list)
    is_abstract_only: bool = False


# ---------------------------------------------------------------------------
# Parsed document
# ---------------------------------------------------------------------------

class ParsedBlock(BaseModel):
    block_id: str
    page_number: int = Field(ge=1)
    block_type: str  # "paragraph", "table", "figure_caption", "section_heading"
    text: str
    bbox: tuple[float, float, float, float] | None = None


class ParsedPage(BaseModel):
    page_number: int = Field(ge=1)
    blocks: list[ParsedBlock] = Field(default_factory=list)
    captions: list[str] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    work_id: str
    file_version: str
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class ReportSection(BaseModel):
    heading: str
    content: str
    order: int


class ResearchReport(BaseModel):
    task_id: str
    version: int = Field(ge=1)
    sections: list[ReportSection] = Field(default_factory=list)
    format: str = "markdown"


# ---------------------------------------------------------------------------
# Pipeline contracts -- lightweight data classes used by Protocols
# ---------------------------------------------------------------------------

class SearchQuery(BaseModel):
    text: str
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    year_policy: str = "recent_5_years_plus_foundational"
    max_results: int = 50


class PaperMetadata(BaseModel):
    work_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    source: str = ""
    document_type: str = ""
    url: str = ""
    language: str = ""
    version_type: VersionType = VersionType.UNKNOWN
    version_id: str = ""


class ScoredPaper(BaseModel):
    metadata: PaperMetadata
    relevance_score: float = 0.0
    authority_score: float = 0.0
    confidence_score: float = 0.0


class ScreeningDecision(BaseModel):
    paper: PaperMetadata
    include: bool
    role_tags: list[str] = Field(default_factory=list)
    reason: str = ""
