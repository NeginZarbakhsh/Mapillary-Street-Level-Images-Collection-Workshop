"""End-to-end test of the API layer, with the Claude call stubbed.

Mirrors the equivalent test in legal-contract-analysis: mixes a genuine
finding with a hallucinated one in the stubbed response, and asserts the
hallucination never survives to the HTTP response.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import analyzer, main  # noqa: E402
from app.models import EvidenceBasis, GovernanceAnalysis, NumericFinding, VotingRule  # noqa: E402
from tests.test_grounding import REAL_QUOTE_12A  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "satzung_bayerischer_junggaertner.pdf"


def _stub_analysis() -> GovernanceAnalysis:
    return GovernanceAnalysis(
        document_type="Association bylaws (Satzung)",
        entity_name="Landesverband Bayerischer Junggärtner e.V.",
        votes_per_ordinary_member=NumericFinding(
            basis=EvidenceBasis.undetermined,
            value=None,
            explanation="Votes are allocated by group/role, not a flat per-member figure.",
            quote=None,
        ),
        max_votes_single_natural_person=NumericFinding(
            # Deliberately the hallucinated pattern the meeting hit.
            basis=EvidenceBasis.stated,
            value="3",
            explanation="Fabricated for this test.",
            quote="No natural person may exercise more than three votes at any single meeting.",
        ),
        plural_voting_exists=NumericFinding(
            basis=EvidenceBasis.stated,
            value="true",
            explanation="Bayerische Jungbauernschaft e.V. holds two block votes.",
            quote="die Bayerische Jungbauernschaft e.V. hat zwei\nStimmen.",
        ),
        voting_rules=[
            VotingRule(
                applies_to="Ortsgruppe",
                rule="One vote per five voting members, cast as a block.",
                votes="1 per 5 members",
                cast_how="Block vote by an authorised representative",
                quote=REAL_QUOTE_12A,
                page=8,
            ),
            VotingRule(
                # Hallucinated: plausible, well-formed, not in the document.
                applies_to="any natural person",
                rule="A natural person may hold at most 3 votes in total.",
                votes="3",
                quote="No natural person may exercise more than three votes at any single meeting.",
                page=8,
            ),
        ],
        control_summary="Control is diffuse: votes are distributed across local groups, the board, and a partner organisation.",
        quorum_and_majority_rules=["Resolutions pass by simple majority of those present."],
        open_questions=["Can one person simultaneously hold a board seat and an Ortsgruppe representative mandate?"],
    )


def test_hallucinated_finding_and_rule_are_stripped_before_the_response(monkeypatch):
    monkeypatch.setattr(
        analyzer, "analyse", lambda document, name, question=None: (_stub_analysis(), "full_document", 5000, 900)
    )

    client = TestClient(main.app)
    with SAMPLE.open("rb") as handle:
        response = client.post(
            "/api/analyse",
            files={"file": ("satzung.pdf", handle, "application/pdf")},
            data={"question": "Wie viele Stimmen hat eine einzelne natürliche Person maximal?"},
        )

    assert response.status_code == 200
    body = response.json()

    # The fabricated voting rule must not reach the response.
    assert len(body["voting_rules"]) == 1
    assert body["discarded_rules"] == 1
    assert body["voting_rules"][0]["applies_to"] == "Ortsgruppe"
    assert body["voting_rules"][0]["anchor"]["page"] == 8

    # The fabricated numeric claim must be downgraded, not shown as fact.
    max_votes = body["max_votes_single_natural_person"]
    assert max_votes["basis"] == "undetermined"
    assert max_votes["value"] is None

    # The genuinely supported numeric claim must survive with its real page.
    plural = body["plural_voting_exists"]
    assert plural["basis"] == "stated"
    assert plural["anchor"]["page"] == 8

    assert body["page_count"] == 10
    assert body["sections_detected"] == 18
    assert body["retrieval_mode"] == "full_document"
