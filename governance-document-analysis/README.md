# Governance Document Analysis — working prototype

Upload association bylaws, articles of association, or a similar
constitutional document. Ask a voting-rights or control question. Get a
structured, page-cited answer where **every claim is either backed by a real
quote or explicitly marked as undetermined** — never a plausible guess.

> Built directly from the meeting brief. `RESEARCH.md` is the assigned
> Azure AI Search vs. MongoDB Atlas comparison; this file is the working code
> and the reasoning behind it.

## Tested against a real document, not a mock

`samples/satzung_bayerischer_junggaertner.pdf` is the actual 10-page statute
shared in the meeting. Every claim in this README about what the pipeline
does was run against that file, not asserted from a synthetic example — see
`tests/`.

---

## 1. Run it

```bash
cd governance-document-analysis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...      # or `ant auth login`

uvicorn app.main:app --reload
```

Open <http://localhost:8000>, drop in `samples/satzung_bayerischer_junggaertner.pdf`,
and ask (or leave blank for the default voting-rights question):

> Wie viele Stimmen hat eine einzelne natürliche Person maximal?

Tests run without any API key or external service — the Claude call is
stubbed, but extraction, chunking and grounding all run for real against the
sample PDF:

```bash
python3 -m pytest tests/ -q
```

---

## 2. Do you need RAG at all?

The meeting spent most of its time on chunking/embeddings/vector-store
architecture. Before adopting that, it's worth checking whether the actual
documents in scope need it.

The sample statute is **10 pages, ~5,000 tokens**. Claude Opus 5 has a
**1,000,000-token context window**. Chunking a document that's under 1% of
the context window doesn't save money or improve quality — it adds a failure
mode that whole-document mode cannot have: **a retrieval miss**, where the
relevant clause silently isn't in the top-k chunks returned to the model, and
the answer is confidently wrong with no visible sign anything was missed.

So this pipeline decides per request:

```
                    token count of the document
                              │
              ┌───────────────┴───────────────┐
        fits in ~60K tokens              too large
              │                                │
     WHOLE-DOCUMENT MODE                RETRIEVAL MODE
     entire text sent to Claude    chunk → embed → store → top-k → Claude
     (default; no retrieval        (only when the document/corpus
      miss is possible)             genuinely doesn't fit)
```

`FULL_DOCUMENT_TOKEN_CEILING` in `app/analyzer.py` sets the threshold — 60K
tokens, chosen so that a genuinely long constitutional document (a few
hundred pages) still gets read in full, while retrieval only engages for the
cases that actually need it: a document too large for one call, or (not yet
wired into the UI, but the vector store supports it) a question spanning many
documents. See `RESEARCH.md` §0 for the full reasoning, and §6 for what the
whole-document path actually produces on the meeting's own worked example.

---

## 3. How it fits together

```
                  ┌───────────────┐
  document  ─────▶│  extract.py   │  PDF/DOCX/TXT → text + a page-offset map
                  └──────┬────────┘  (page numbers are the citation unit here)
                         │
                         ▼
                  ┌───────────────┐
                  │ analyzer.py   │  count tokens → whole-doc, or:
                  │ choose_context│
                  └──────┬────────┘
              too large  │
                         ▼
          ┌───────────────────────────┐
          │  chunker.py                │  split on § / Article / Clause
          │  embeddings.py              │  boundaries (paragraph-anchored,
          │  vectorstore.py              │  so in-text citations like
          │                               │  "§ 670 BGB" don't false-split)
          └──────────────┬────────────┘  embed chunks, rank by similarity
                         │
                         ▼
                  ┌───────────────┐
                  │ analyzer.py   │  messages.parse(output_format=
                  │   analyse()   │    GovernanceAnalysis), adaptive thinking
                  └──────┬────────┘
                         │
                         ▼
                  ┌───────────────┐
                  │verify_quote /  │  every quote searched for in the
                  │verify_*_finding│  source; unsupported claims discarded
                  └──────┬────────┘  or downgraded to "undetermined"
                         ▼
                  ┌───────────────┐
                  │   main.py     │  FastAPI ──▶ static/index.html
                  └───────────────┘
```

| File | Responsibility |
|---|---|
| `app/extract.py` | File → text, with a byte-offset → page-number map |
| `app/chunker.py` | Section-aware splitting (`§ N`, `Article N`, `Clause N`), with a paragraph-boundary check that rejects in-text statute citations |
| `app/embeddings.py` | Pluggable embedder — Voyage AI (real) or a local hash vectoriser (testing only, no API key) |
| `app/vectorstore.py` | `VectorStore` interface — local in-memory (real, tested), Azure AI Search / MongoDB Atlas adapters (reference implementations, **not exercised** — no live resource in this environment) |
| `app/models.py` | Pydantic schema. `basis: stated / inferable / undetermined` on every numeric claim is the field that stops the model from guessing |
| `app/analyzer.py` | Context selection, the Claude call, and quote/page verification |
| `app/main.py` | HTTP endpoints |
| `static/index.html` | Upload UI, single page, no build step |

---

## 4. The schema decision that matters

A bare `max_votes_single_person: int` field invites the model to fill it with
something plausible even when the document doesn't say. That's the literal
failure the meeting's own worked example caught — a draft answer of `3` with
no clause behind it (`RESEARCH.md` §6 works through what the real document
actually supports).

So every numeric or boolean claim in `GovernanceAnalysis` is a `NumericFinding`
with a required `basis`:

| `basis` | Meaning | `value` / `quote` |
|---|---|---|
| `stated` | One passage says this directly | Required — verified against the source |
| `inferable` | Combines ≥2 explicit passages | Required — verified against the source |
| `undetermined` | The document does not say | Both null — cannot be guessed |

The system prompt tells the model this distinction exists and that "the
document doesn't say" is a correct, expected answer for the hardest question
(role-stacking) — not a failure to find something. Reaching for a specific
number when none is supported is the wrong answer, not a more helpful one.

**Then the server checks anyway.** `verify_numeric_finding` and
`verify_voting_rules` in `app/analyzer.py` search the source document for
every quote a `stated`/`inferable` finding claims. A finding whose quote
can't be located is:

- a **voting rule** → discarded, with the count surfaced (`discarded_rules`)
- a **numeric finding** → downgraded to `undetermined`, with an explanation of
  what was rejected and why, rather than silently dropped

This is the same three-tier matcher (exact → typographic-fold → whitespace-
collapse, never paraphrase) validated in the companion `legal-contract-analysis`
prototype, extended here to also resolve the source **page number** from the
match offset.

`tests/test_grounding.py` and `tests/test_pipeline.py` run this exact scenario
against the real sample document: a fabricated "max 3 votes" claim mixed with
a genuine one, asserting the fabricated one never reaches the API response.

---

## 5. Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Or `ant auth login` |
| `GOVERNANCE_MODEL` | `claude-opus-5` | |
| `GOVERNANCE_EFFORT` | `high` | |
| `VOYAGE_API_KEY` | unset | Unset = falls back to a deterministic local hash embedder for the retrieval path (mechanism-testing only, not semantically meaningful — see `app/embeddings.py`) |
| `EMBEDDING_MODEL` | `voyage-3-large` | Anthropic's recommended embedding partner |
| `VECTOR_STORE` | `local` | `local` \| `azure` \| `mongodb` — see `RESEARCH.md` for when to change this |
| `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_KEY` / `AZURE_SEARCH_INDEX` | — | Only read when `VECTOR_STORE=azure` |
| `MONGODB_URI` / `MONGODB_DB` / `MONGODB_COLLECTION` | — | Only read when `VECTOR_STORE=mongodb` |

---

## 6. API

| Endpoint | Purpose |
|---|---|
| `GET /` | The UI |
| `GET /api/health` | Model, effort, credentials, active vector store / embedding provider |
| `POST /api/analyse` | multipart: `file` + optional `question` → full structured analysis |

---

## 7. Answering the meeting's action items

**Negin's assigned items:**

- ✅ Research Azure AI Search (pricing, search units, suitability) — `RESEARCH.md` §1
- ✅ Research MongoDB Atlas (vector search, ingestion, pricing) — `RESEARCH.md` §2
- ✅ Compare both, plus the existing custom pipeline — `RESEARCH.md` §3–5
- ✅ Working code for the extraction schema and the retrieval mechanism (this repo)

**Mahdie's assigned items** (share existing prototype, prompts, sample
contracts, implementation details) are his to provide — nothing here
substitutes for that; the schema and grounding approach in `app/models.py`
and `app/analyzer.py` are designed to be easy to reconcile with whatever his
prototype already does once it's shared.

---

## 8. What this does not do

- **No OCR** — scanned PDFs are rejected with a clear message.
- **Retrieval mode is unexercised end-to-end with real embeddings** — the
  mechanism (chunk → embed → store → rank) is tested and proven to retrieve
  the right section (see `tests/`), but only with the local hash embedder;
  semantic quality with real Voyage embeddings needs a `VOYAGE_API_KEY` this
  environment doesn't have.
- **Azure AI Search and MongoDB Atlas adapters are reference code, not
  verified code.** Written against each SDK's current, documented surface,
  but never run against a live resource — there isn't one in this
  environment. Test against a real instance before trusting either in
  production.
- **No live Claude call was made anywhere in building this** — there's no API
  key in this environment. Every claim about the pipeline's behaviour above
  is backed by a test that runs deterministically against the real sample
  document; the model call itself is stubbed in `tests/test_pipeline.py`.
- **Single document per request.** No cross-document corpus search yet, even
  though `VectorStore.query` already supports it (`doc_id=None`) — wiring a
  corpus-search endpoint is the natural next step once there's more than one
  document to search.
- **Not legal advice.**

---

## 9. Next steps

1. Get a `VOYAGE_API_KEY` and confirm retrieval quality with real embeddings,
   not the hash fallback.
2. Once Mahdie shares his prototype, reconcile schemas and prompts rather than
   maintaining two.
3. Wire a corpus-search endpoint (`VectorStore.query` already supports
   `doc_id=None` across documents).
4. Test the Azure and MongoDB adapters against real resources before relying
   on either — see §8.
5. OCR for scanned constitutional documents.
6. A real evaluation set: several statutes, hand-checked answers, tracked
   `discarded_rules` / `undetermined` rate over time as a regression signal.
