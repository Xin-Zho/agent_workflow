"""Mock PDF parser using PyMuPDF for test PDFs, fallback for others."""

from workflow_models import ParsedDocument, ParsedPage, ParsedBlock


class MockDocumentParser:
    async def parse(self, pdf_bytes: bytes) -> ParsedDocument:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for i, page in enumerate(doc):
                blocks = []
                block_idx = 0
                for b in page.get_text("blocks"):
                    if b[4].strip():
                        blocks.append(ParsedBlock(
                            block_id=f"p{i+1}-b{block_idx}",
                            page_number=i + 1,
                            block_type="paragraph",
                            text=b[4].strip(),
                            bbox=(b[0], b[1], b[2], b[3]),
                        ))
                        block_idx += 1
                pages.append(ParsedPage(
                    page_number=i + 1,
                    blocks=blocks,
                    captions=[],
                    tables=[],
                ))
            doc.close()
            return ParsedDocument(work_id="", file_version="", pages=pages)
        except Exception:
            return ParsedDocument(work_id="", file_version="")
