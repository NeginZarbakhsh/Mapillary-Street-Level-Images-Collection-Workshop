"""Pydantic schemas for contract analysis.

These classes are the contract between Claude and the rest of the app: they are
passed straight to `client.messages.parse(output_format=...)`, so the model is
constrained to emit exactly this shape. Every field description below is read by
the model, so they double as prompt text -- keep them precise.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class RiskCategory(str, Enum):
    liability = "liability"
    indemnity = "indemnity"
    termination = "termination"
    auto_renewal = "auto_renewal"
    payment = "payment"
    intellectual_property = "intellectual_property"
    confidentiality = "confidentiality"
    data_protection = "data_protection"
    warranty = "warranty"
    dispute_resolution = "dispute_resolution"
    assignment_change_of_control = "assignment_change_of_control"
    restrictive_covenant = "restrictive_covenant"
    service_levels = "service_levels"
    other = "other"


class Party(BaseModel):
    name: str = Field(description="Legal entity name exactly as written in the contract.")
    role: str = Field(description="Role in this contract, e.g. 'Supplier', 'Customer', 'Licensor'.")
    registered_details: Optional[str] = Field(
        default=None,
        description="Company number, registered address or similar, if stated. Null if absent.",
    )


class KeyTerms(BaseModel):
    contract_type: str = Field(description="e.g. 'Master Services Agreement', 'NDA', 'SaaS Subscription'.")
    parties: List[Party]
    effective_date: Optional[str] = Field(default=None, description="As written. Null if not stated.")
    initial_term: Optional[str] = Field(default=None, description="e.g. '24 months from the Effective Date'.")
    renewal: Optional[str] = Field(default=None, description="Renewal mechanics, including notice periods.")
    governing_law: Optional[str] = None
    jurisdiction: Optional[str] = None
    total_value: Optional[str] = Field(default=None, description="Contract value or pricing basis, as written.")
    payment_terms: Optional[str] = None
    plain_english_summary: str = Field(
        description="Six to ten sentences a non-lawyer can act on. No legalese, no hedging."
    )


class Finding(BaseModel):
    """A single risk. `quote` is load-bearing: it is verified against the source."""

    title: str = Field(description="Short label, under 12 words.")
    severity: Severity
    category: RiskCategory
    clause_reference: Optional[str] = Field(
        default=None, description="Clause or section number as printed, e.g. '11.3'. Null if unnumbered."
    )
    quote: str = Field(
        description=(
            "VERBATIM contiguous text copied character-for-character from the contract, "
            "20-400 characters, that this finding is based on. Never paraphrase, never "
            "join separated passages, never fix typos. This is checked against the source "
            "and the finding is discarded if it does not match."
        )
    )
    what_it_means: str = Field(description="Plain-English reading of the quoted text.")
    why_it_matters: str = Field(description="Concrete commercial or legal exposure this creates.")
    recommendation: str = Field(description="Specific redline or negotiation ask.")
    playbook_rule: Optional[str] = Field(
        default=None, description="ID of the playbook rule this breaches, if any. Null otherwise."
    )


class Obligation(BaseModel):
    responsible_party: str = Field(description="Which named party owes this.")
    obligation: str = Field(description="What they must do.")
    trigger_or_deadline: Optional[str] = Field(
        default=None, description="When it bites, e.g. 'within 30 days of invoice'. Null if open-ended."
    )
    clause_reference: Optional[str] = None
    quote: str = Field(description="VERBATIM supporting text, as per the Finding.quote rules.")


class MissingProtection(BaseModel):
    clause_type: str = Field(description="The protection that is absent, e.g. 'Limitation of liability cap'.")
    why_it_matters: str
    suggested_position: str = Field(description="The clause to ask for, in one or two sentences.")


class ContractAnalysis(BaseModel):
    """Top-level object returned by the model for one contract."""

    key_terms: KeyTerms
    findings: List[Finding] = Field(description="Ordered most severe first.")
    obligations: List[Obligation]
    missing_protections: List[MissingProtection]
    overall_assessment: str = Field(
        description="Three to five sentences: would you sign this as drafted, and what are the blockers?"
    )


# ---------------------------------------------------------------------------
# API-layer models (not sent to Claude)
# ---------------------------------------------------------------------------


class Anchor(BaseModel):
    """Where a verified quote actually sits in the extracted text."""

    start: int
    end: int
    exact: bool = Field(description="True for a character-exact match, False for a whitespace-normalised match.")


class VerifiedFinding(Finding):
    anchor: Optional[Anchor] = None
    anchor_status: str = Field(description="'verified' or 'unverified'.")


class VerifiedObligation(Obligation):
    anchor: Optional[Anchor] = None
    anchor_status: str


class AnalysisResponse(BaseModel):
    document_name: str
    character_count: int
    input_tokens: int
    output_tokens: int
    key_terms: KeyTerms
    findings: List[VerifiedFinding]
    obligations: List[VerifiedObligation]
    missing_protections: List[MissingProtection]
    overall_assessment: str
    discarded_findings: int = Field(
        description="Findings dropped because their quote could not be located in the source."
    )
    source_text: str
