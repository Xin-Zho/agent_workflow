import pytest
from pydantic import ValidationError
from workflow_models import (
    TaskDefinition, Metric, EvidenceLocator, CompositionRatio,
    RatioBasis, SampleExtraction, PaperStatus, JobStatus, VersionType,
    ProcessStep, PerformanceMetric, TestCondition, ParsedBlock, ParsedPage,
    ParsedDocument, ResearchReport, ReportSection,
)


class TestTaskDefinition:
    def test_minimal_valid(self):
        d = TaskDefinition(
            research_object="test material",
            application="test app",
            target_metrics=[Metric(name="strength", unit="MPa", target_range=">100")],
            hard_constraints=["lab feasible"],
            optimization_objectives=["max strength"],
            acceptable_tradeoffs=["cost"],
        )
        assert d.paper_target == 10  # default
        assert d.languages == ["zh", "en"]

    def test_invalid_paper_target(self):
        with pytest.raises(ValidationError):
            TaskDefinition(
                research_object="x", application="y",
                target_metrics=[], hard_constraints=[], optimization_objectives=[],
                acceptable_tradeoffs=[], paper_target=3,  # below ge=5
            )

    def test_list_defaults_are_independent(self):
        a = TaskDefinition(
            research_object="x", application="y",
            target_metrics=[], hard_constraints=[], optimization_objectives=[],
            acceptable_tradeoffs=[],
        )
        b = TaskDefinition(
            research_object="x", application="y",
            target_metrics=[], hard_constraints=[], optimization_objectives=[],
            acceptable_tradeoffs=[],
        )
        a.languages.append("fr")
        assert "fr" not in b.languages


class TestEvidenceLocator:
    def test_source_types(self):
        for st in ["explicit", "derived", "estimated", "inferred", "missing"]:
            e = EvidenceLocator(
                evidence_id="EV-001",
                field_path="samples/S1/ratios/1/raw_value",
                work_id="doi:10.1000/test",
                file_version="v1",
                page=5,
                source_type=st,
            )
            assert e.source_type == st

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            EvidenceLocator(
                evidence_id="EV-001",
                field_path="x",
                work_id="x",
                file_version="x",
                page=1,
                source_type="fabricated",
            )

    def test_optional_fields(self):
        e = EvidenceLocator(
            evidence_id="EV-001",
            field_path="x",
            work_id="x",
            file_version="x",
            page=1,
            source_type="explicit",
            section="2.3",
            figure="Figure 4",
            table="Table 2",
            quote_or_value="10 wt%",
        )
        assert e.section == "2.3"
        assert e.figure == "Figure 4"


class TestCompositionRatio:
    def test_default_ratio_basis(self):
        r = CompositionRatio(component="PEO", raw_value="10", raw_unit="wt%")
        assert r.ratio_basis == RatioBasis.UNSPECIFIED

    def test_evidence_ids(self):
        r = CompositionRatio(
            component="PEO", raw_value="10", raw_unit="wt%",
            evidence_ids=["EV-001", "EV-002"],
        )
        assert len(r.evidence_ids) == 2

    def test_default_evidence_ids(self):
        r = CompositionRatio(component="PEO", raw_value="10", raw_unit="wt%")
        assert r.evidence_ids == []


class TestSampleExtraction:
    def test_full_sample(self):
        evidence = [
            EvidenceLocator(
                evidence_id="EV-001", field_path="samples/S1/ratios/1/raw_value",
                work_id="doi:x", file_version="v1", page=5, source_type="explicit",
            ),
            EvidenceLocator(
                evidence_id="EV-002", field_path="samples/S1/performance/1/value",
                work_id="doi:x", file_version="v1", page=7, source_type="estimated",
            ),
        ]
        sample = SampleExtraction(
            sample_id="S1",
            components=[{"name": "PEO", "role": "matrix"}],
            ratios=[CompositionRatio(
                component="PEO", raw_value="10", raw_unit="wt%",
                evidence_ids=["EV-001"],
            )],
            performance_metrics=[PerformanceMetric(
                property="conductivity", value="5e-3", unit="S/cm",
                evidence_ids=["EV-002"],
            )],
            evidence=evidence,
        )
        assert sample.sample_id == "S1"
        assert sample.is_abstract_only is False
        # Verify mixed evidence per field
        assert sample.ratios[0].evidence_ids == ["EV-001"]
        assert sample.performance_metrics[0].evidence_ids == ["EV-002"]

    def test_abstract_only(self):
        s = SampleExtraction(sample_id="S1", is_abstract_only=True)
        assert s.is_abstract_only is True
        assert s.ratios == []


class TestEnums:
    def test_paper_status_values(self):
        assert PaperStatus.CANDIDATE == "candidate"
        assert PaperStatus.SELECTED == "selected"
        assert PaperStatus.FETCHED == "fetched"
        assert PaperStatus.PARSED == "parsed"
        assert PaperStatus.EXTRACTED == "extracted"
        assert PaperStatus.DEGRADED == "degraded"
        assert PaperStatus.FAILED == "failed"

    def test_job_status_values(self):
        assert JobStatus.QUEUED == "queued"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.RETRY_WAIT == "retry_wait"
        assert JobStatus.DEAD_LETTER == "dead_letter"

    def test_version_type_values(self):
        assert VersionType.PREPRINT == "preprint"
        assert VersionType.JOURNAL == "journal"
        assert VersionType.CORRIGENDUM == "corrigendum"
        assert VersionType.UNKNOWN == "unknown"


class TestParsedDocument:
    def test_minimal(self):
        doc = ParsedDocument(work_id="doi:x", file_version="v1")
        assert doc.pages == []

    def test_with_blocks(self):
        block = ParsedBlock(
            block_id="p3-b12", page_number=3, block_type="paragraph",
            text="The conductivity was measured.",
        )
        page = ParsedPage(page_number=3, blocks=[block])
        doc = ParsedDocument(work_id="doi:x", file_version="v1", pages=[page])
        assert doc.pages[0].blocks[0].block_id == "p3-b12"
