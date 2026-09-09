"""End-to-end test of everything except the network call to Claude.

`analyzer.analyse` is stubbed with a response that deliberately mixes genuine
findings with a hallucinated one, so the test proves the thing that actually
protects the user: fabricated findings never reach the API response.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import analyzer, main  # noqa: E402
from app.models import (  # noqa: E402
    ContractAnalysis,
    Finding,
    KeyTerms,
    MissingProtection,
    Obligation,
    Party,
    RiskCategory,
    Severity,
)

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_msa.txt"


def _stub_analysis() -> ContractAnalysis:
    return ContractAnalysis(
        key_terms=KeyTerms(
            contract_type="Master Services Agreement",
            parties=[
                Party(name="NORTHWIND FITNESS LIMITED", role="Customer"),
                Party(name="MERIDIAN E-CO LIMITED", role="Supplier"),
            ],
            effective_date="3 March 2026",
            initial_term="36 months",
            governing_law="Delaware",
            plain_english_summary="A three-year services agreement heavily weighted to the Customer.",
        ),
        findings=[
            # Genuine: this text is in the sample verbatim.
            Finding(
                title="Supplier liability is unlimited",
                severity=Severity.critical,
                category=RiskCategory.liability,
                clause_reference="7.2",
                quote="the Supplier's total aggregate liability arising out",
                what_it_means="The Supplier carries uncapped liability.",
                why_it_matters="Existential exposure on a services deal.",
                recommendation="Cap at 12 months of fees.",
                playbook_rule="LIA-01",
            ),
            # Genuine, but citing a playbook rule that does not exist.
            Finding(
                title="Seven day renewal notice",
                severity=Severity.high,
                category=RiskCategory.auto_renewal,
                clause_reference="2.2",
                quote="written notice of non-renewal not less than seven (7) days",
                what_it_means="Only a week to stop a two-year renewal.",
                why_it_matters="Easy to miss, locks in 24 months.",
                recommendation="Extend to 90 days.",
                playbook_rule="ZZZ-99",
            ),
            # Hallucinated: plausible, well-formed, and nowhere in the document.
            Finding(
                title="Insurance requirement is onerous",
                severity=Severity.high,
                category=RiskCategory.other,
                clause_reference="15.4",
                quote="The Supplier shall maintain professional indemnity insurance of not less than EUR 10,000,000.",
                what_it_means="A 10m insurance floor applies.",
                why_it_matters="Premiums would exceed deal margin.",
                recommendation="Reduce to EUR 2m.",
                playbook_rule=None,
            ),
        ],
        obligations=[
            Obligation(
                responsible_party="Customer",
                obligation="Pay invoices",
                trigger_or_deadline="within 7 days of invoice date",
                clause_reference="3.2",
                quote="Invoices are payable within seven (7) days of the date of invoice.",
            ),
        ],
        missing_protections=[
            MissingProtection(
                clause_type="Mutual limitation of liability",
                why_it_matters="Only the Customer's liability is capped.",
                suggested_position="Add a symmetrical cap at 12 months of fees.",
            )
        ],
        overall_assessment="Not signable as drafted.",
    )


def test_hallucinated_finding_is_dropped(monkeypatch):
    monkeypatch.setattr(analyzer, "count_input_tokens", lambda text, name: 1000)
    monkeypatch.setattr(analyzer, "analyse", lambda text, name: (_stub_analysis(), 5000, 900))

    client = TestClient(main.app)
    with SAMPLE.open("rb") as handle:
        response = client.post("/api/analyse", files={"file": ("sample_msa.txt", handle, "text/plain")})

    assert response.status_code == 200
    body = response.json()

    # Three findings went in; the fabricated one must not come out.
    assert len(body["findings"]) == 2
    assert body["discarded_findings"] == 1
    titles = [f["title"] for f in body["findings"]]
    assert "Insurance requirement is onerous" not in titles

    # Surviving findings are anchored at offsets that really contain their quote.
    source = body["source_text"]
    for finding in body["findings"]:
        assert finding["anchor_status"] == "verified"
        anchor = finding["anchor"]
        span = " ".join(source[anchor["start"] : anchor["end"]].split())
        assert span == " ".join(finding["quote"].split())


def test_invented_playbook_rule_is_stripped(monkeypatch):
    monkeypatch.setattr(analyzer, "count_input_tokens", lambda text, name: 1000)
    monkeypatch.setattr(analyzer, "analyse", lambda text, name: (_stub_analysis(), 5000, 900))

    client = TestClient(main.app)
    with SAMPLE.open("rb") as handle:
        body = client.post(
            "/api/analyse", files={"file": ("sample_msa.txt", handle, "text/plain")}
        ).json()

    by_title = {f["title"]: f for f in body["findings"]}
    assert by_title["Supplier liability is unlimited"]["playbook_rule"] == "LIA-01"
    # ZZZ-99 is not a real rule, so it must be cleared rather than shown as policy.
    assert by_title["Seven day renewal notice"]["playbook_rule"] is None


def test_obligations_are_anchored(monkeypatch):
    monkeypatch.setattr(analyzer, "count_input_tokens", lambda text, name: 1000)
    monkeypatch.setattr(analyzer, "analyse", lambda text, name: (_stub_analysis(), 5000, 900))

    client = TestClient(main.app)
    with SAMPLE.open("rb") as handle:
        body = client.post(
            "/api/analyse", files={"file": ("sample_msa.txt", handle, "text/plain")}
        ).json()

    assert len(body["obligations"]) == 1
    assert body["obligations"][0]["anchor_status"] == "verified"
