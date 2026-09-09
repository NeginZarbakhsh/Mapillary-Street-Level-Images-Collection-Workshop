"""Split a document into retrievable chunks.

Two strategies:

- `split_by_section`: German statutes (and most association bylaws / articles of
  association / contracts) are organised under numbered headings (`§ 12`,
  `Article 12`, `Clause 12`). Splitting on those boundaries keeps a whole clause
  together, which matters here specifically because voting-rights rules are
  usually one dense clause (see `§ 12 Stimmrecht` in the sample) that must not be
  cut in half -- a chunk boundary landing mid-clause is a silent way to lose the
  answer.
- `split_fixed`: fallback for documents with no detected heading pattern.

Both return `Chunk` objects carrying the page range they came from, using the
`Document.page_of()` offset map from `extract.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .extract import Document

# Matches "§ 12", "§12", "Article 12", "Clause 12", "Section 12" at a plausible
# heading position. Deliberately permissive: better to over-split (small extra
# chunks) than to miss a boundary and merge two clauses into one chunk.
_HEADING_RE = re.compile(
    r"(§\s*\d+[a-zA-Z]?|Article\s+\d+|Clause\s+\d+|Section\s+\d+)\s+[A-ZÄÖÜ][\wÄÖÜäöüß .,/&-]{2,60}",
)


@dataclass
class Chunk:
    id: str
    heading: str
    text: str
    start: int
    end: int
    page_start: int
    page_end: int


def _starts_a_paragraph(text: str, position: int) -> bool:
    """True if `position` sits at the start of the document or right after a
    blank line.

    Without this check, an in-text citation like "Aufwendungsersatzanspruch
    nach § 670 BGB" is indistinguishable from a real heading -- both match
    `§ \\d+ <capitalised word>`. Real headings in a statute begin a fresh
    paragraph; citations to other statutes (BGB, StGB, ...) sit mid-sentence.
    Anchoring on paragraph position rather than blacklisting "BGB" also
    catches citations to any other code, not just the ones seen in this file.
    """
    before = text[:position].rstrip(" \t")
    return before == "" or before.endswith("\n\n") or before.endswith("\n")


def split_by_section(document: Document, doc_id: str = "doc") -> Optional[List[Chunk]]:
    """Split on numbered-heading boundaries. Returns None if no headings found."""
    all_matches = list(_HEADING_RE.finditer(document.text))
    matches = [m for m in all_matches if _starts_a_paragraph(document.text, m.start())]
    if len(matches) < 2:
        # Need at least 2 to trust this is really a heading pattern and not a
        # coincidental match (e.g. a single "§ 5" mentioned in running prose).
        return None

    chunks: List[Chunk] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(document.text)
        text = document.text[start:end].strip()
        if not text:
            continue
        heading = match.group(0).strip()
        chunks.append(
            Chunk(
                id=f"{doc_id}-s{i}",
                heading=heading,
                text=text,
                start=start,
                end=end,
                page_start=document.page_of(start),
                page_end=document.page_of(end - 1),
            )
        )
    return chunks


def split_fixed(document: Document, doc_id: str = "doc", size: int = 1500, overlap: int = 200) -> List[Chunk]:
    """Fixed-size fallback for documents without a detectable heading pattern."""
    chunks: List[Chunk] = []
    start = 0
    i = 0
    while start < len(document.text):
        end = min(start + size, len(document.text))
        text = document.text[start:end]
        chunks.append(
            Chunk(
                id=f"{doc_id}-c{i}",
                heading=f"[untitled section {i + 1}]",
                text=text,
                start=start,
                end=end,
                page_start=document.page_of(start),
                page_end=document.page_of(max(start, end - 1)),
            )
        )
        i += 1
        if end == len(document.text):
            break
        start = end - overlap
    return chunks


def chunk_document(document: Document, doc_id: str = "doc") -> List[Chunk]:
    """Preferred entry point: try section splitting, fall back to fixed-size."""
    sections = split_by_section(document, doc_id)
    if sections:
        return sections
    return split_fixed(document, doc_id)
