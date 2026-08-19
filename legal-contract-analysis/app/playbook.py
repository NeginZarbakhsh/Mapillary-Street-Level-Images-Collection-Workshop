"""The negotiation playbook.

This is the difference between "an LLM summarised your contract" and "a review".
A playbook encodes the positions your business actually holds: what you accept,
what you push back on, and where you walk. Claude is asked to test the contract
against these rules and cite the rule ID it breaches.

Edit RULES to match your own standard positions -- nothing else needs to change.
The rules are rendered into the system prompt at request time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Rule:
    id: str
    topic: str
    position: str
    breach_severity: str  # must be one of the Severity enum values


RULES: List[Rule] = [
    Rule(
        id="LIA-01",
        topic="Limitation of liability",
        position=(
            "Our aggregate liability must be capped. A cap at or below 12 months of "
            "fees paid is acceptable; an uncapped or supra-fee cap is not."
        ),
        breach_severity="critical",
    ),
    Rule(
        id="LIA-02",
        topic="Consequential loss",
        position="Both parties must exclude indirect and consequential loss. A one-way exclusion favouring the counterparty is a breach.",
        breach_severity="high",
    ),
    Rule(
        id="IND-01",
        topic="Indemnities",
        position=(
            "Indemnities we give must be limited to IP infringement and breach of "
            "confidentiality, and must be capped. Uncapped or broad general indemnities are a breach."
        ),
        breach_severity="critical",
    ),
    Rule(
        id="TRM-01",
        topic="Termination for convenience",
        position="We must be able to terminate for convenience on no more than 90 days' written notice.",
        breach_severity="high",
    ),
    Rule(
        id="TRM-02",
        topic="Auto-renewal",
        position=(
            "Automatic renewal is acceptable only if the notice window to prevent renewal "
            "is 30 days or longer. Anything shorter, or renewal for more than 12 months, is a breach."
        ),
        breach_severity="high",
    ),
    Rule(
        id="PAY-01",
        topic="Payment terms",
        position="Payment terms of 30 days or longer from a valid invoice. Shorter terms, or payment on order, are a breach.",
        breach_severity="medium",
    ),
    Rule(
        id="PAY-02",
        topic="Price increases",
        position="Any uplift must be capped (e.g. CPI or 5%) and require prior written notice. Uncapped or unilateral increases are a breach.",
        breach_severity="high",
    ),
    Rule(
        id="IP-01",
        topic="Ownership of deliverables",
        position="We retain ownership of our pre-existing IP and tooling. Assignment of our background IP is a breach.",
        breach_severity="critical",
    ),
    Rule(
        id="DP-01",
        topic="Data protection",
        position=(
            "Where personal data is processed there must be a DPA or equivalent processing "
            "terms, and any international transfer mechanism must be named. Silence is a breach."
        ),
        breach_severity="high",
    ),
    Rule(
        id="DR-01",
        topic="Governing law and forum",
        position="Governing law and forum must be England and Wales or Ireland. Any other forum is a breach.",
        breach_severity="medium",
    ),
    Rule(
        id="ASG-01",
        topic="Assignment and change of control",
        position="The counterparty must not assign without our consent. A unilateral assignment right, or a change-of-control termination right against us, is a breach.",
        breach_severity="medium",
    ),
    Rule(
        id="RES-01",
        topic="Restrictive covenants",
        position="No exclusivity, non-compete or non-solicit binding us beyond 12 months post-termination.",
        breach_severity="high",
    ),
]


def render() -> str:
    """Format the playbook for inclusion in the system prompt."""
    lines = []
    for rule in RULES:
        lines.append(
            f"- {rule.id} ({rule.topic}) [breach severity: {rule.breach_severity}]: {rule.position}"
        )
    return "\n".join(lines)


VALID_RULE_IDS = {rule.id for rule in RULES}
