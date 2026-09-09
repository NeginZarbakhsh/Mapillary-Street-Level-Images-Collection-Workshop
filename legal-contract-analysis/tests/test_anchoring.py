"""Tests for quote anchoring.

Anchoring is the safety property of this app: a finding survives only if its
quote is genuinely present in the contract. These tests cover the tiers the
matcher is allowed to relax, and -- more importantly -- what it must still reject.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analyzer import verify_quote  # noqa: E402
from app.extract import normalise  # noqa: E402

SOURCE = (
    "7.2 Subject to clause 7.1, the Supplier's total aggregate liability arising out\n"
    "    of or in connection with this Agreement shall be unlimited.\n"
    "\n"
    "8.1 The Supplier shall indemnify and hold harmless the Customer against all\n"
    "    losses, damages, costs and expenses of any kind whatsoever.\n"
)


def test_exact_match_is_exact():
    quote = "shall be unlimited."
    anchor = verify_quote(quote, SOURCE)
    assert anchor is not None
    assert anchor.exact is True
    assert SOURCE[anchor.start : anchor.end] == quote


def test_quote_spanning_a_line_break_is_found():
    # The model quotes a sentence; the source has a newline and indent inside it.
    quote = "the Supplier's total aggregate liability arising out of or in connection with this Agreement shall be unlimited."
    anchor = verify_quote(quote, SOURCE)
    assert anchor is not None
    assert anchor.exact is False
    # The anchor must still bracket the real text in the original string.
    span = " ".join(SOURCE[anchor.start : anchor.end].split())
    assert span == quote


def test_typographic_apostrophe_is_folded():
    source = "the Supplier’s total aggregate liability shall be unlimited."
    anchor = verify_quote("the Supplier's total aggregate liability", source)
    assert anchor is not None
    assert anchor.exact is False


def test_paraphrase_is_rejected():
    # Same meaning, different words. This is the case that must never pass.
    assert verify_quote("the Supplier has unlimited liability under this contract", SOURCE) is None


def test_invented_clause_is_rejected():
    assert verify_quote("The Supplier shall maintain insurance of not less than EUR 5,000,000.", SOURCE) is None


def test_stitched_quote_is_rejected():
    # Text from clause 7.2 joined to text from clause 8.1.
    stitched = "shall be unlimited. The Supplier shall indemnify and hold harmless"
    assert verify_quote(stitched, SOURCE) is None


def test_too_short_quote_is_rejected():
    assert verify_quote("shall", SOURCE) is None


def test_anchor_offsets_are_valid_into_the_source():
    quote = "indemnify and hold harmless the Customer"
    anchor = verify_quote(quote, SOURCE)
    assert anchor is not None
    assert 0 <= anchor.start < anchor.end <= len(SOURCE)


def test_normalise_does_not_shift_content_within_a_line():
    text = "1.1  The Supplier shall provide   the Services.   \n\n\n\n2.1 Term."
    out = normalise(text)
    assert "The Supplier shall provide   the Services." in out
    assert "\n\n\n" not in out


def test_real_sample_contract_anchors():
    sample = Path(__file__).resolve().parent.parent / "samples" / "sample_msa.txt"
    text = normalise(sample.read_text())
    quote = "renew automatically for successive periods of twenty-four (24) months"
    anchor = verify_quote(quote, text)
    assert anchor is not None
    assert " ".join(text[anchor.start : anchor.end].split()) == quote
