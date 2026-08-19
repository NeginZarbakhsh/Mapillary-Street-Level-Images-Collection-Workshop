"""Pydantic schemas for governance/voting-rights extraction.

Passed straight to `client.messages.parse(output_format=...)`. Field
descriptions double as prompt text -- the model reads them.

Design note driven directly by the meeting's own example: a naive schema with
a bare `max_votes_single_person: int` invites the model to guess a plausible
number even when the document doesn't actually state one (the meeting's own
worked example flagged this: "3" was not defensible from the text). So every
numeric or boolean claim here is paired with a `basis` field that forces an
explicit choice between "the text states this directly" and "this is inferred
/ cannot be determined" -- there is no way to emit a bare number with no
accountability for where it came from.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class EvidenceBasis(str, Enum):
    stated = "stated"  # the document says this explicitly
    inferable = "inferable"  # follows from combining >=2 explicit statements
    undetermined = "undetermined"  # the document does not say; do not guess


class VotingRule(BaseModel):
    """One allocation-of-votes rule, e.g. one lettered sub-clause of a
    Stimmrecht / voting-rights section."""

    applies_to: str = Field(description="Who or what this rule allocates votes to, e.g. 'Ortsgruppe', 'Landesvorstand member'.")
    rule: str = Field(description="Plain-English statement of the rule.")
    votes: Optional[str] = Field(
        default=None,
        description="The vote count or formula as written, e.g. '1 per started 5 members', '2'. Null if not numeric.",
    )
    cast_how: Optional[str] = Field(
        default=None,
        description="How the vote is exercised if stated -- individually, as a block by a representative, by proxy, etc. Null if not stated.",
    )
    quote: str = Field(
        description=(
            "VERBATIM contiguous text copied character-for-character from the document, "
            "15-400 characters, supporting this rule. Never paraphrase, never join text "
            "from two locations. This is checked against the source; the rule is discarded "
            "if the quote does not match."
        )
    )
    page: Optional[int] = Field(default=None, description="Page number the quote appears on, if known.")


class NumericFinding(BaseModel):
    """A single yes/no or numeric answer, always paired with its basis."""

    basis: EvidenceBasis
    value: Optional[str] = Field(
        default=None,
        description="The answer as a string (e.g. '1', '3', 'true'), or null when basis is 'undetermined'.",
    )
    explanation: str = Field(
        description=(
            "Why this basis was chosen. If 'inferable', name the specific rules combined. "
            "If 'undetermined', state what is missing -- e.g. 'the statute does not say "
            "whether one person may simultaneously hold a board seat and a representative "
            "mandate, so no ceiling can be derived.'"
        )
    )
    quote: Optional[str] = Field(
        default=None,
        description="VERBATIM supporting quote, required when basis is 'stated' or 'inferable'. Null when 'undetermined'.",
    )
    page: Optional[int] = None


class GovernanceAnalysis(BaseModel):
    """Top-level object returned by the model for one governance document."""

    document_type: str = Field(description="e.g. 'Association bylaws (Satzung)', 'Shareholders' agreement', 'Partnership deed'.")
    entity_name: Optional[str] = Field(default=None, description="Name of the organisation, as written.")

    votes_per_ordinary_member: NumericFinding = Field(
        description="Votes held by a single ordinary/individual member acting alone, if that concept applies here."
    )
    max_votes_single_natural_person: NumericFinding = Field(
        description=(
            "The MAXIMUM number of votes one natural person could exercise, accounting for "
            "every role they could simultaneously hold (member, officer, appointed "
            "representative, proxy-holder). Mark 'undetermined' rather than guessing if the "
            "document does not address whether roles can be combined."
        )
    )
    plural_voting_exists: NumericFinding = Field(
        description="Whether any member/class holds more than one vote, or an enhanced/weighted vote."
    )

    voting_rules: List[VotingRule] = Field(description="Every distinct vote-allocation rule found, in document order.")

    control_summary: str = Field(
        description=(
            "Three to six sentences: who can, in practice, control decisions -- by vote "
            "concentration, veto rights, appointment powers, or quorum/majority thresholds. "
            "State plainly if control is genuinely diffuse."
        )
    )
    quorum_and_majority_rules: List[str] = Field(
        default_factory=list,
        description="Quorum requirements and majority thresholds for key decisions (ordinary resolutions, amendments, dissolution), as short plain-English statements.",
    )

    open_questions: List[str] = Field(
        default_factory=list,
        description="Specific follow-up questions a reviewer should ask to resolve any 'undetermined' finding above.",
    )


# ---------------------------------------------------------------------------
# API-layer models (post-verification)
# ---------------------------------------------------------------------------


class Anchor(BaseModel):
    start: int
    end: int
    page: int
    exact: bool


class VerifiedVotingRule(VotingRule):
    anchor: Optional[Anchor] = None
    anchor_status: str


class VerifiedNumericFinding(NumericFinding):
    anchor: Optional[Anchor] = None
    anchor_status: str  # "verified" | "unverified" | "not_applicable" (undetermined, no quote to check)


class AnalysisResponse(BaseModel):
    document_name: str
    page_count: int
    sections_detected: int
    retrieval_mode: str = Field(description="'full_document' or 'retrieval' -- see README for when each is used.")
    input_tokens: int
    output_tokens: int

    document_type: str
    entity_name: Optional[str]
    votes_per_ordinary_member: VerifiedNumericFinding
    max_votes_single_natural_person: VerifiedNumericFinding
    plural_voting_exists: VerifiedNumericFinding
    voting_rules: List[VerifiedVotingRule]
    control_summary: str
    quorum_and_majority_rules: List[str]
    open_questions: List[str]
    discarded_rules: int
