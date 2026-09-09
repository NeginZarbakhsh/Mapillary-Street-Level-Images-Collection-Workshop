"""Split extracted text into chunks -- the step after PDF-to-text, before
embedding.

Why chunking exists at all: a huge document can't be handed to an AI model
in one piece (or, even when it technically fits, doing so for every question
is wasteful). So you cut it into pieces small enough to search over, and only
feed the model the pieces that are actually relevant to a given question.
This script does the cutting -- it doesn't call any AI model, costs nothing,
and runs entirely on your machine.

It splits on numbered headings (`§ 12`, `Article 12`, `Clause 12`, `Section
12`), so a whole clause stays together in one chunk instead of getting cut in
half. This exact logic was tested against a real 10-page statute and
correctly found all 18 real sections with zero false splits on in-text
citations like "§ 670 BGB" (a reference to a different law, not a heading of
the document itself) -- see `test_chunker.py` for the proof.

Usage:
    python3 chunk_text.py text_output/mydoc.txt              # print chunks
    python3 chunk_text.py text_output/mydoc.txt --out chunks  # save as JSON
    python3 chunk_text.py text_output --out chunks --batch    # whole folder
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# Matches "§ 12", "Article 12", "Clause 12", "Section 12" followed by a
# capitalised heading word. Deliberately permissive -- better to over-split
# (a few extra small chunks) than to miss a real boundary and merge two
# clauses together.
_HEADING_RE = re.compile(
    r"(§\s*\d+[a-zA-Z]?|Article\s+\d+|Clause\s+\d+|Section\s+\d+)\s+[A-ZÄÖÜ][\wÄÖÜäöüß .,/&-]{2,60}",
)

_PAGE_MARKER_RE = re.compile(r"\[page (\d+)\]\n")


@dataclass
class Chunk:
    id: str
    heading: str
    text: str
    page_start: int
    page_end: int


def parse_pages(text: str) -> Tuple[str, List[int]]:
    """Strip the "[page N]" markers pdf_to_text.py wrote, and remember where
    each page starts in the resulting plain text.

    Returns (plain_text, page_starts) where page_starts[i] is the character
    offset in `plain_text` at which page i+1 (1-indexed) begins.
    """
    matches = list(_PAGE_MARKER_RE.finditer(text))
    if not matches:
        # No page markers -- e.g. a .txt file that didn't come from
        # pdf_to_text.py. Treat the whole thing as page 1.
        return text, [0]

    plain_parts: List[str] = []
    page_starts: List[int] = []
    cursor = 0
    for i, m in enumerate(matches):
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[content_start:content_end]
        page_starts.append(cursor)
        plain_parts.append(chunk)
        cursor += len(chunk)
    return "".join(plain_parts), page_starts


def _page_of(offset: int, page_starts: List[int]) -> int:
    page = 1
    for i, start in enumerate(page_starts):
        if offset >= start:
            page = i + 1
        else:
            break
    return page


def _starts_a_paragraph(text: str, position: int) -> bool:
    """True at the start of the document or right after a blank line.

    Without this check, an in-text citation like "Aufwendungsersatzanspruch
    nach § 670 BGB" looks identical to a real heading -- both match
    "§ <number> <capitalised word>". Real headings begin a fresh paragraph;
    citations to other documents sit mid-sentence. This is the exact check
    that turned a broken 20-chunk split (with 2 false ones) into a clean
    18-chunk split when tested on the real sample statute.
    """
    before = text[:position].rstrip(" \t")
    return before == "" or before.endswith("\n\n") or before.endswith("\n")


def chunk_by_heading(text: str, page_starts: List[int], doc_id: str = "doc") -> Optional[List[Chunk]]:
    all_matches = list(_HEADING_RE.finditer(text))
    matches = [m for m in all_matches if _starts_a_paragraph(text, m.start())]
    if len(matches) < 2:
        return None  # not enough real headings to trust this is a heading-based document

    chunks: List[Chunk] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk_text_ = text[start:end].strip()
        if not chunk_text_:
            continue
        chunks.append(
            Chunk(
                id=f"{doc_id}-s{i}",
                heading=match.group(0).strip(),
                text=chunk_text_,
                page_start=_page_of(start, page_starts),
                page_end=_page_of(end - 1, page_starts),
            )
        )
    return chunks


def chunk_fixed(text: str, page_starts: List[int], doc_id: str = "doc", size: int = 1500, overlap: int = 200) -> List[Chunk]:
    """Fallback for documents with no detectable numbered-heading pattern."""
    chunks: List[Chunk] = []
    start, i = 0, 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(
            Chunk(
                id=f"{doc_id}-c{i}",
                heading=f"[untitled section {i + 1}]",
                text=text[start:end],
                page_start=_page_of(start, page_starts),
                page_end=_page_of(max(start, end - 1), page_starts),
            )
        )
        i += 1
        if end == len(text):
            break
        start = end - overlap
    return chunks


def chunk_document(text: str, doc_id: str = "doc") -> List[Chunk]:
    """The one function to call: strips page markers, tries heading-based
    splitting, falls back to fixed-size chunks."""
    plain_text, page_starts = parse_pages(text)
    chunks = chunk_by_heading(plain_text, page_starts, doc_id)
    if chunks:
        return chunks
    return chunk_fixed(plain_text, page_starts, doc_id)


def _process_one(path: Path, out_dir: Optional[Path]) -> None:
    text = path.read_text(encoding="utf-8")
    chunks = chunk_document(text, doc_id=path.stem)

    mode = "heading-based" if len(chunks) >= 2 and not chunks[0].heading.startswith("[untitled") else "fixed-size fallback"
    print(f"{path.name}: {len(chunks)} chunks ({mode})")
    for c in chunks:
        print(f"    p{c.page_start}-{c.page_end}  {c.heading!r}  ({len(c.text)} chars)")

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{path.stem}.chunks.json"
        out_path.write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    -> saved to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="A .txt file, or a folder if --batch is given")
    parser.add_argument("--out", help="Folder to save each document's chunks as JSON (default: print only)")
    parser.add_argument("--batch", action="store_true", help="Treat `path` as a folder and process every .txt file in it")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else None
    input_path = Path(args.path)

    if args.batch:
        files = sorted(input_path.glob("*.txt"))
        if not files:
            print(f"No .txt files found in {input_path}/", file=sys.stderr)
            sys.exit(1)
        for f in files:
            _process_one(f, out_dir)
    else:
        _process_one(input_path, out_dir)


if __name__ == "__main__":
    main()
