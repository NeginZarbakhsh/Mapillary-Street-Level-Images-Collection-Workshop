"""Section chunking, tested against the real sample statute -- not synthetic
text -- because the failure mode that matters (a false split on an in-text
citation like "§ 670 BGB") only shows up in real documents.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.chunker import chunk_document  # noqa: E402
from app.extract import extract  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "satzung_bayerischer_junggaertner.pdf"


def _document():
    return extract("satzung.pdf", SAMPLE.read_bytes())


def test_all_eighteen_sections_detected_with_no_false_splits():
    chunks = chunk_document(_document(), "satzung")
    headings = [c.heading for c in chunks]
    # Exactly 18 numbered sections in this statute (§1 .. §18). A false split
    # on an in-text civil-code citation ("§ 670 BGB", "§ 31a BGB") would show
    # up as extra chunks with a heading that isn't a real § N of this document.
    assert len(chunks) == 18
    assert headings[0].startswith("§ 1 ")
    assert headings[-1].startswith("§ 18")
    assert not any("BGB" in h for h in headings)


def test_voting_rights_section_is_a_single_intact_chunk():
    chunks = chunk_document(_document(), "satzung")
    s12 = next(c for c in chunks if c.heading.startswith("§ 12"))
    # All six lettered sub-rules (a-f) must survive in one chunk -- a boundary
    # landing between them would silently drop part of the answer.
    for letter_rule in ["a.)", "b.)", "c.)", "d.)", "e.)", "f.)"]:
        assert letter_rule in s12.text
    assert "Stimmübertragung" in s12.text


def test_voting_rights_section_lands_on_the_expected_page():
    chunks = chunk_document(_document(), "satzung")
    s12 = next(c for c in chunks if c.heading.startswith("§ 12"))
    # The meeting's own worked example cites "sources: page 8" for this clause.
    assert s12.page_start == 8


def test_fixed_fallback_used_when_no_headings_present():
    from app.chunker import split_fixed
    from app.extract import Document

    doc = Document(text="just plain prose with no section markers at all. " * 50, page_starts=[0])
    chunks = split_fixed(doc, "plain", size=200, overlap=20)
    assert len(chunks) > 1
    assert chunks[0].start == 0
    assert chunks[-1].end == len(doc.text)
