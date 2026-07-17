"""Mock search provider returning fixed papers from fixtures/papers.json."""

import json
import os
from pathlib import Path

from workflow_models import SearchQuery, PaperMetadata, VersionType


class MockSearchProvider:
    def __init__(self, fixture_path: str | None = None):
        if fixture_path is None:
            fixture_path = str(
                Path(__file__).resolve().parent.parent / "fixtures" / "papers.json"
            )
        with open(fixture_path, encoding="utf-8") as f:
            self._papers = json.load(f)

    async def search(self, query: SearchQuery) -> list[PaperMetadata]:
        results = []
        for p in self._papers[: query.max_results]:
            results.append(PaperMetadata(
                work_id=p["work_id"],
                title=p["title"],
                authors=p.get("authors", []),
                year=p.get("year"),
                abstract=p.get("abstract", ""),
                doi=p.get("doi", ""),
                source=p.get("source", "mock"),
                document_type=p.get("document_type", "article"),
                url=p.get("url", ""),
                language=p.get("language", "en"),
                version_type=VersionType(p.get("version_type", "journal")),
                version_id=p.get("version_id", "v1"),
            ))
        return results


class MockFulltextProvider:
    async def fetch(self, paper: PaperMetadata) -> bytes | None:
        fixture_pdf = (
            Path(__file__).resolve().parent.parent / "fixtures" / "sample_material.pdf"
        )
        if fixture_pdf.exists():
            return fixture_pdf.read_bytes()
        return None
