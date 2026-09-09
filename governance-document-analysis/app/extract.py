"""PDF/DOCX/TXT to text, keeping a page map.

Page numbers are the citation unit for governance documents -- "sources: page 8"
is a requirement from the brief, not a nicety. So this module returns both the
concatenated text (what gets chunked / sent to Claude) and a byte-offset ->
page-number map, the same offset-mapping technique used for quote anchoring in
the contract-review prototype (`legal-contract-analysis/app/analyzer.py`).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import List, Tuple

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


class ExtractionError(RuntimeError):
    pass


@dataclass
class Document:
    text: str
    # page_starts[i] = offset in `text` where page i+1 (1-indexed) begins.
    page_starts: List[int]

    def page_of(self, offset: int) -> int:
        """1-indexed page number containing this character offset."""
        page = 1
        for i, start in enumerate(self.page_starts):
            if offset >= start:
                page = i + 1
            else:
                break
        return page

    @property
    def page_count(self) -> int:
        return len(self.page_starts)


def _pdf_pages(data: bytes) -> List[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ExtractionError("PDF is password protected.") from exc

    pages = [page.extract_text() or "" for page in reader.pages]
    if sum(len(p.replace(" ", "").replace("\n", "")) for p in pages) < 200:
        raise ExtractionError(
            "Almost no text found. This looks like a scanned PDF -- it needs OCR "
            "before it can be analysed."
        )
    return pages


def _docx_pages(data: bytes) -> List[str]:
    # DOCX has no fixed page boundaries (pagination is a rendering concern), so
    # the whole document is treated as a single "page" for citation purposes.
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"Could not open DOCX: {exc}") from exc

    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return ["\n".join(parts)]


def _text_pages(data: bytes) -> List[str]:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return [data.decode(encoding)]
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Could not decode file as text.")


def _clean_page(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(filename: str, data: bytes) -> Document:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in SUPPORTED:
        raise ExtractionError(
            f"Unsupported file type '{suffix or filename}'. Supported: {', '.join(sorted(SUPPORTED))}"
        )

    if suffix == ".pdf":
        raw_pages = _pdf_pages(data)
    elif suffix == ".docx":
        raw_pages = _docx_pages(data)
    else:
        raw_pages = _text_pages(data)

    pages = [_clean_page(p) for p in raw_pages]
    if not any(pages):
        raise ExtractionError("File contained no readable text.")

    text_parts: List[str] = []
    page_starts: List[int] = []
    cursor = 0
    for page in pages:
        page_starts.append(cursor)
        text_parts.append(page)
        cursor += len(page) + 2  # matches the "\n\n" join below
    text = "\n\n".join(text_parts)

    return Document(text=text, page_starts=page_starts)
