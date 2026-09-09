"""Claude integration: the extraction call, and grounding every claim back to
a real page in the source document.

Same principle as the commercial contract-review prototype
(`legal-contract-analysis/app/analyzer.py`): structured output guarantees the
*shape* of the answer, not its truth. A schema can force the model to emit
`max_votes_single_natural_person`, but it cannot stop the model from putting a
plausible-looking "3" there when the document does not actually support one --
which is precisely the failure the meeting's own worked example ran into.

So every quote the model returns is searched for in the actual page text, and
anything that doesn't verify is dropped (voting rules) or downgraded before
the user sees it (numeric findings) rather than shown as fact.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

import anthropic

from .chunker import Chunk, chunk_document
from .extract import Document
from .models import (
    Anchor,
    EvidenceBasis,
    GovernanceAnalysis,
    NumericFinding,
    VerifiedNumericFinding,
    VerifiedVotingRule,
    VotingRule,
)

MODEL = os.environ.get("GOVERNANCE_MODEL", "claude-opus-5")
EFFORT = os.environ.get("GOVERNANCE_EFFORT", "high")
MAX_TOKENS = 24_000

# Whole-document mode is used up to this size. Opus 5 has a 1M context window;
# this ceiling is deliberately far below that -- it is not a technical limit,
# it is the point past which "read the whole thing" is no longer materially
# better than retrieval and cost starts to matter. See README "Do you need
# RAG at all?" for the reasoning.
FULL_DOCUMENT_TOKEN_CEILING = 60_000
RETRIEVAL_TOP_K = 8


SYSTEM_PROMPT = """\
You are a governance analyst. You read association bylaws, articles of \
association, shareholders' agreements and similar constitutional documents \
and answer precise questions about voting rights and control.

READ EVERYTHING BEFORE ANSWERING

Voting-rights sections routinely allocate votes by role, not by person: a \
member gets one vote, a group gets a block vote cast by its representative, an \
office-holder gets a vote in that capacity. The question "how many votes can \
one person have" is a question about whether roles can be COMBINED, which is \
usually answered nowhere near the voting clause -- check the sections on \
membership, board composition and delegate selection too.

THE EVIDENCE RULE

Every `quote` field must be text copied character-for-character from the \
document. The application searches the source for that exact string; a quote \
that is not found causes the finding to be discarded before anyone reads it. \
So: copy, do not retype; quote one contiguous passage, never stitch two \
together; keep it under ~400 characters.

WHEN YOU DO NOT KNOW

This is the most important instruction in this prompt. If the document does \
not explicitly state something, or if it can only be worked out by assuming \
facts the document does not confirm (e.g. "can one person legally hold two of \
these roles at once?"), set `basis` to "undetermined", leave `value` and \
`quote` null, and explain exactly what information is missing in \
`explanation`. A wrong specific answer is worse than an honest "the document \
does not say" -- do not fill the gap with a plausible-sounding guess.

Use "stated" only when one passage says the answer directly. Use "inferable" \
only when you are combining two or more passages you can each quote -- name \
which ones in `explanation`. `voting_rules` should capture every distinct \
allocation rule you find, in document order, each with its own quote and page.
"""

USER_TEMPLATE = """\
Document: {name}
{context_note}

<document>
{text}
</document>

Question to prioritise (still complete every field in the schema): {question}
"""

DEFAULT_QUESTION = (
    "What are the voting rights in this document, and what is the maximum "
    "number of votes a single natural person could hold?"
)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


class AnalysisError(RuntimeError):
    pass


def count_input_tokens(text: str, name: str, question: str) -> int:
    client = _client()
    result = client.messages.count_tokens(
        model=MODEL,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_TEMPLATE.format(name=name, context_note="", text=text, question=question),
            }
        ],
    )
    return result.input_tokens


def choose_context(document: Document, name: str, question: str) -> Tuple[str, str, List[Chunk]]:
    """Decide full-document vs. retrieval mode, and build the context string.

    Returns (context_text, mode, chunks_used). `chunks_used` is empty in
    full-document mode.
    """
    try:
        tokens = count_input_tokens(document.text, name, question)
    except Exception:
        tokens = None  # count_tokens is a nicety; fall through to a length heuristic

    fits = tokens is not None and tokens <= FULL_DOCUMENT_TOKEN_CEILING
    if fits or (tokens is None and len(document.text) < FULL_DOCUMENT_TOKEN_CEILING * 3):
        return document.text, "full_document", []

    # Retrieval mode: chunk, embed, rank, concatenate the top matches with
    # their page ranges labelled so the model can cite them.
    from .embeddings import get_embedder
    from .vectorstore import get_vector_store

    chunks = chunk_document(document, doc_id=name)
    embedder = get_embedder()
    vectors = embedder.embed([c.text for c in chunks])
    store = get_vector_store()
    store.upsert(name, chunks, vectors)

    [qvec] = embedder.embed([question])
    results = store.query(qvec, top_k=RETRIEVAL_TOP_K, doc_id=name)
    matched_ids = {r.id for r in results}
    used = [c for c in chunks if c.id in matched_ids]

    context = "\n\n---\n\n".join(
        f"[pages {c.page_start}-{c.page_end}]\n{c.text}" for c in used
    )
    return context, "retrieval", used


def analyse(
    document: Document, name: str, question: Optional[str] = None
) -> Tuple[GovernanceAnalysis, str, int, int]:
    """Run the extraction. Returns (analysis, retrieval_mode, input_tokens, output_tokens)."""
    question = question or DEFAULT_QUESTION
    context_text, mode, _chunks = choose_context(document, name, question)
    context_note = (
        "(Below is the section of the document retrieved as most relevant to the "
        "question. It may not be the whole document -- if something looks "
        "incomplete, say so in `open_questions` rather than guessing.)"
        if mode == "retrieval"
        else ""
    )

    client = _client()
    try:
        response = client.with_options(timeout=600.0).messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            output_format=GovernanceAnalysis,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[
                {
                    "role": "user",
                    "content": USER_TEMPLATE.format(
                        name=name, context_note=context_note, text=context_text, question=question
                    ),
                }
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
        raise AnalysisError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY or run `ant auth login`."
        ) from exc

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        category = getattr(detail, "category", None) if detail else None
        raise AnalysisError(f"The model declined to complete this request (category: {category or 'unspecified'}).")
    if response.stop_reason == "max_tokens":
        raise AnalysisError("The analysis was cut off before it finished. Raise MAX_TOKENS and try again.")

    analysis = response.parsed_output
    if analysis is None:
        raise AnalysisError("The model returned no parseable analysis.")

    return analysis, mode, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# Grounding -- same three-tier matcher as legal-contract-analysis/analyzer.py,
# extended to also resolve the source page from the offset map.
# ---------------------------------------------------------------------------

_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",  # single quotes
        "“": '"', "”": '"', "„": '"',  # double quotes
        "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",  # dashes
        " ": " ", " ": " ", " ": " ",  # non-breaking / thin spaces
        "…": "...",  # ellipsis (length-preserving via 3 chars -> guarded below)
    }
)


def _fold(text: str) -> str:
    folded = text.translate(_FOLD)
    return folded if len(folded) == len(text) else text


def _collapse_with_map(text: str) -> Tuple[str, List[int]]:
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


def verify_quote(quote: Optional[str], document: Document) -> Optional[Anchor]:
    if not quote:
        return None
    quote = quote.strip()
    if len(quote) < 8:
        return None

    position = document.text.find(quote)
    if position != -1:
        return Anchor(start=position, end=position + len(quote), page=document.page_of(position), exact=True)

    folded_source = _fold(document.text)
    folded_quote = _fold(quote)
    position = folded_source.find(folded_quote)
    if position != -1:
        return Anchor(start=position, end=position + len(folded_quote), page=document.page_of(position), exact=False)

    collapsed_source, index_map = _collapse_with_map(folded_source)
    collapsed_quote = " ".join(folded_quote.split())
    if not collapsed_quote:
        return None
    position = collapsed_source.find(collapsed_quote)
    if position == -1:
        return None

    start = index_map[position]
    end = index_map[position + len(collapsed_quote) - 1] + 1
    return Anchor(start=start, end=end, page=document.page_of(start), exact=False)


def verify_voting_rules(rules: List[VotingRule], document: Document) -> Tuple[List[VerifiedVotingRule], int]:
    kept: List[VerifiedVotingRule] = []
    discarded = 0
    for rule in rules:
        anchor = verify_quote(rule.quote, document)
        if anchor is None:
            discarded += 1
            continue
        kept.append(VerifiedVotingRule(**rule.model_dump(), anchor=anchor, anchor_status="verified"))
    return kept, discarded


def verify_numeric_finding(finding: NumericFinding, document: Document) -> VerifiedNumericFinding:
    if finding.basis == "undetermined":
        return VerifiedNumericFinding(**finding.model_dump(), anchor=None, anchor_status="not_applicable")

    anchor = verify_quote(finding.quote, document)
    if anchor is None:
        # The model claimed evidence but the quote doesn't check out. Downgrade
        # rather than silently keep a "stated" claim with no real support --
        # showing it as verified would be exactly the misgrounding failure this
        # whole design exists to prevent.
        downgraded = finding.model_copy(
            update={
                "basis": EvidenceBasis.undetermined,
                "value": None,
                "explanation": (
                    f"Discarded: the model claimed basis='{finding.basis.value}' with quote "
                    f"{finding.quote!r}, but that text could not be located in the source "
                    "document. Original explanation: " + finding.explanation
                ),
                "quote": None,
            }
        )
        return VerifiedNumericFinding(**downgraded.model_dump(), anchor=None, anchor_status="unverified")

    return VerifiedNumericFinding(**finding.model_dump(), anchor=anchor, anchor_status="verified")
