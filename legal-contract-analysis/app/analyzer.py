"""Claude integration and grounding.

Two jobs:

1. Ask Claude for a structured review of a contract (`analyse`).
2. Prove that every risk it reports is anchored in text that actually appears in
   the document (`verify_quote`).

Job 2 is the reason this is usable for legal work. Structured output guarantees
the *shape* of the answer, not its truthfulness -- a model can still produce a
fluent finding attached to a clause that was never in the contract. So the schema
forces every finding to carry a verbatim `quote`, and this module locates that
quote in the source. Findings whose quote cannot be located are dropped before
they ever reach the user, and the count of dropped findings is surfaced.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

import anthropic

from . import playbook
from .models import (
    Anchor,
    ContractAnalysis,
    Finding,
    Obligation,
    VerifiedFinding,
    VerifiedObligation,
)

MODEL = os.environ.get("CONTRACT_MODEL", "claude-opus-5")
EFFORT = os.environ.get("CONTRACT_EFFORT", "high")

# Adaptive thinking plus a large-but-not-streaming-sized output cap. Thinking
# tokens count against max_tokens, so this needs headroom above the JSON itself.
MAX_TOKENS = 32_000

# Opus 5 has a 1M context window. This is a guard against sending something
# absurd (a 4,000-page bundle) rather than a real ceiling.
MAX_INPUT_TOKENS = 700_000


SYSTEM_PROMPT = """\
You are a contract review assistant supporting a commercial team. You review \
contracts from the perspective of OUR side of the deal and report what a \
competent commercial lawyer would flag on a first pass.

HOW TO WORK

Read the whole document before you report anything. Contracts qualify \
themselves across clauses: a liability cap in clause 11 is often gutted by a \
carve-out in clause 19, and a termination right in clause 4 may be conditioned \
in a schedule. Report the combined effect, not the first clause you meet.

Test the contract against the playbook below. Where a clause breaches a rule, \
set `playbook_rule` to that rule's ID and use the rule's stated breach severity. \
Where you flag something the playbook does not cover, leave `playbook_rule` null \
and use your own judgement on severity.

Severity means exposure to us, not how unusual the drafting is:
- critical: uncapped or existential exposure, or a term that would block signature.
- high: materially bad, needs to be negotiated before signing.
- medium: worth pushing on, would not on its own stop a deal.
- low: housekeeping, note it and move on.

EVIDENCE RULE -- THIS IS ENFORCED

Every finding and every obligation must carry a `quote` field containing text \
copied character-for-character from the contract. The application searches the \
source document for that exact string. If it is not found, your finding is \
deleted before anyone reads it.

So:
- Copy, do not retype and do not tidy. Keep the original spelling, casing, \
punctuation and any typos.
- Quote one contiguous passage. Never stitch together text from two places.
- Keep it between roughly 20 and 400 characters -- enough to stand alone, not a \
whole clause.
- If you cannot support a point with a real quote, do not make the point.

For `missing_protections` there is nothing to quote, because the point is that \
the text is absent. Only list a protection as missing after you have checked the \
whole document, including any schedules, for it.

Be specific and be brief. "Clause 11.2 caps liability at the fees paid in the \
preceding three months, which on this deal is about EUR 12,000" is useful. \
"Liability provisions should be reviewed" is not.

OUR PLAYBOOK
{playbook}
"""

USER_TEMPLATE = """\
Review the following contract and return your analysis in the required structure.

Document name: {name}

<contract>
{text}
</contract>
"""


def _client() -> anthropic.Anthropic:
    # Zero-arg construction resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or
    # an `ant auth login` profile, in that order.
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Quote anchoring
# ---------------------------------------------------------------------------

# PDF extraction produces typographic characters; models routinely emit the ASCII
# equivalent when quoting. Folding both sides is a 1:1 character substitution, so
# character offsets are preserved and stay valid against the original string.
_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"',
        "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
        " ": " ", " ": " ", " ": " ",
        "…": "...",
    }
)


def _fold(text: str) -> str:
    folded = text.translate(_FOLD)
    # "…" -> "..." is the one substitution that changes length, which would break
    # offset mapping. Guard rather than silently corrupt the anchors.
    return folded if len(folded) == len(text) else text


def _collapse_with_map(text: str) -> Tuple[str, List[int]]:
    """Collapse whitespace runs to a single space, keeping an index map back.

    Returns the collapsed string and a list where `index_map[i]` is the offset in
    `text` of the character that produced `collapsed[i]`.
    """
    chars: List[str] = []
    index_map: List[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            chars.append(" ")
            index_map.append(i)
            prev_space = True
        else:
            chars.append(ch)
            index_map.append(i)
            prev_space = False
    return "".join(chars), index_map


def verify_quote(quote: str, source: str) -> Optional[Anchor]:
    """Locate `quote` in `source`, tolerating whitespace and typography drift.

    Returns None when the quote is not present, which is the signal that the
    finding is unsupported and should be discarded.
    """
    quote = quote.strip()
    if len(quote) < 8:
        # Too short to be meaningful evidence, and near-certain to match by chance.
        return None

    # Tier 1: character-exact.
    position = source.find(quote)
    if position != -1:
        return Anchor(start=position, end=position + len(quote), exact=True)

    # Tier 2: typography folded. Length-preserving, so offsets still line up.
    folded_source = _fold(source)
    folded_quote = _fold(quote)
    position = folded_source.find(folded_quote)
    if position != -1:
        return Anchor(start=position, end=position + len(folded_quote), exact=False)

    # Tier 3: whitespace collapsed. Handles line breaks and PDF column artefacts
    # landing in the middle of a quoted sentence.
    collapsed_source, index_map = _collapse_with_map(folded_source)
    collapsed_quote = " ".join(folded_quote.split())
    position = collapsed_source.find(collapsed_quote)
    if position == -1 or not collapsed_quote:
        return None

    start = index_map[position]
    end = index_map[position + len(collapsed_quote) - 1] + 1
    return Anchor(start=start, end=end, exact=False)


def verify_findings(findings: List[Finding], source: str) -> Tuple[List[VerifiedFinding], int]:
    kept: List[VerifiedFinding] = []
    discarded = 0
    for finding in findings:
        anchor = verify_quote(finding.quote, source)
        if anchor is None:
            discarded += 1
            continue
        # A rule ID the model invented is worse than no rule ID: it implies a
        # policy decision nobody wrote down.
        rule = finding.playbook_rule
        if rule is not None and rule not in playbook.VALID_RULE_IDS:
            rule = None
        kept.append(
            VerifiedFinding(
                **{**finding.model_dump(), "playbook_rule": rule},
                anchor=anchor,
                anchor_status="verified",
            )
        )
    return kept, discarded


def verify_obligations(obligations: List[Obligation], source: str) -> List[VerifiedObligation]:
    # Obligations are kept even when unanchored -- a missed deadline is a worse
    # outcome than an unhighlighted one -- but they are labelled so the UI can
    # show that the evidence did not check out.
    result: List[VerifiedObligation] = []
    for obligation in obligations:
        anchor = verify_quote(obligation.quote, source)
        result.append(
            VerifiedObligation(
                **obligation.model_dump(),
                anchor=anchor,
                anchor_status="verified" if anchor else "unverified",
            )
        )
    return result


# ---------------------------------------------------------------------------
# The API call
# ---------------------------------------------------------------------------


class AnalysisError(RuntimeError):
    pass


def count_input_tokens(text: str, name: str) -> int:
    """Pre-flight size check.

    Never truncate a contract to make it fit -- a review of an unknown subset of
    a contract is worse than no review, because it reads like a complete one.
    """
    client = _client()
    result = client.messages.count_tokens(
        model=MODEL,
        system=SYSTEM_PROMPT.format(playbook=playbook.render()),
        messages=[{"role": "user", "content": USER_TEMPLATE.format(name=name, text=text)}],
    )
    return result.input_tokens



def analyse(text: str, name: str) -> Tuple[ContractAnalysis, int, int]:
    """Run the review. Returns (analysis, input_tokens, output_tokens)."""
    client = _client()

    try:
        response = client.with_options(timeout=900.0).messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            output_format=ContractAnalysis,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT.format(playbook=playbook.render()),
                    # The system prompt and playbook are identical on every
                    # request, so caching them means only the contract itself is
                    # charged at full rate on repeat reviews.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {"role": "user", "content": USER_TEMPLATE.format(name=name, text=text)}
            ],
        )
    except anthropic.NotFoundError as exc:
        raise AnalysisError(f"Model '{MODEL}' is not available to this account.") from exc
    except anthropic.RateLimitError as exc:
        raise AnalysisError("Rate limited by the Claude API. Retry shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError(f"Could not reach the Claude API: {exc}") from exc
    except TypeError as exc:
        # The SDK raises TypeError when no credential source resolves.
        raise AnalysisError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY or run `ant auth login`."
        ) from exc

    # Always check stop_reason before trusting content.
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        category = getattr(detail, "category", None) if detail else None
        raise AnalysisError(
            f"The model declined to complete this request (category: {category or 'unspecified'})."
        )
    if response.stop_reason == "max_tokens":
        raise AnalysisError(
            "The review was cut off before it finished. Raise MAX_TOKENS or lower "
            "CONTRACT_EFFORT and try again."
        )

    analysis = response.parsed_output
    if analysis is None:
        raise AnalysisError("The model returned no parseable analysis.")

    return analysis, response.usage.input_tokens, response.usage.output_tokens
