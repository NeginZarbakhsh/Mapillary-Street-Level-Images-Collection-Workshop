"""Grounding tests against the real sample statute.

The central property under test: a finding is only ever shown as fact when
the exact text it claims to quote is really in the document. This directly
tests the failure the meeting's own worked example ran into --
`max_votes_single_person: 3` with no real basis in the text.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analyzer import verify_numeric_finding, verify_quote, verify_voting_rules  # noqa: E402
from app.extract import extract  # noqa: E402
from app.models import EvidenceBasis, NumericFinding, VotingRule  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "satzung_bayerischer_junggaertner.pdf"

REAL_QUOTE_12A = (
    "Pro angefangenen fünf stimmberechtigten Mitgliedern hat jede Ortsgruppe eine\nStimme."
)


def _document():
    return extract("satzung.pdf", SAMPLE.read_bytes())


def test_real_quote_from_voting_section_anchors_to_page_8():
    anchor = verify_quote(REAL_QUOTE_12A, _document())
    assert anchor is not None
    assert anchor.page == 8
    assert anchor.exact is True


def test_the_meetings_flagged_hallucination_is_rejected():
    # This is the literal failure the meeting's worked example hit: a specific,
    # plausible-sounding vote cap that the statute does not actually state.
    hallucinated = VotingRule(
        applies_to="any natural person",
        rule="A natural person may hold at most 3 votes in total.",
        votes="3",
        quote="No natural person may exercise more than three votes at any single meeting.",
    )
    real = VotingRule(
        applies_to="Ortsgruppe",
        rule="One vote per five voting members.",
        votes="1 per 5 members",
        quote=REAL_QUOTE_12A,
    )
    kept, discarded = verify_voting_rules([hallucinated, real], _document())
    assert discarded == 1
    assert len(kept) == 1
    assert kept[0].applies_to == "Ortsgruppe"
    assert kept[0].anchor.page == 8


def test_stated_finding_with_fabricated_quote_is_downgraded_to_undetermined():
    bad = NumericFinding(
        basis=EvidenceBasis.stated,
        value="3",
        explanation="The statute caps individual voting power at three votes.",
        quote="No natural person may exercise more than three votes at any single meeting.",
    )
    verified = verify_numeric_finding(bad, _document())
    assert verified.basis == EvidenceBasis.undetermined
    assert verified.value is None
    assert verified.anchor_status == "unverified"
    assert "Discarded" in verified.explanation


def test_genuinely_undetermined_finding_passes_through_unchanged():
    honest = NumericFinding(
        basis=EvidenceBasis.undetermined,
        value=None,
        explanation="The statute does not state whether one person may hold multiple roles at once.",
        quote=None,
    )
    verified = verify_numeric_finding(honest, _document())
    assert verified.basis == EvidenceBasis.undetermined
    assert verified.anchor_status == "not_applicable"


def test_inferable_finding_with_real_quote_is_kept():
    ok = NumericFinding(
        basis=EvidenceBasis.stated,
        value="2",
        explanation="Explicitly stated for Bayerische Jungbauernschaft e.V.",
        quote="die Bayerische Jungbauernschaft e.V. hat zwei\nStimmen.",
    )
    verified = verify_numeric_finding(ok, _document())
    assert verified.basis == EvidenceBasis.stated
    assert verified.anchor_status == "verified"
    assert verified.anchor.page == 8
