# Contract Review — working prototype

Upload a contract, get a structured review where **every finding is anchored to
verbatim text in the document**. Built on Claude Opus 5.

> Not legal advice. This is a triage tool that helps a human review faster; a
> human still makes the signing decision.

![status](https://img.shields.io/badge/status-prototype-blue)

- **`RESEARCH.md`** — the market/technical research, and why the design is shaped this way.
- **This file** — what it does, how to run it, and how each piece works.

---

## 1. What it does

Give it a PDF, DOCX or TXT contract and it returns:

| Output | Description |
|---|---|
| **Key terms** | Type, parties, dates, term, renewal, governing law, value, payment terms, plain-English summary |
| **Risk findings** | Severity-ranked, each with a verbatim quote, what it means, why it matters, and the redline to ask for |
| **Playbook breaches** | Which of *our* standard positions the contract violates, by rule ID |
| **Obligations** | Who owes what, by when |
| **Missing protections** | Clauses that should be there and are not |

The single design commitment that makes it usable: **a finding that cannot be
tied to real text in the document is deleted before you see it**, and the count
of deleted findings is shown. See §4.

---

## 2. Run it

```bash
cd legal-contract-analysis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...      # or run `ant auth login`

uvicorn app.main:app --reload
```

Open <http://localhost:8000> and drop in a contract. There is a deliberately
awful sample at `samples/sample_msa.txt` to try it on — it breaches most of the
playbook.

Run the tests (no API key needed — the model call is stubbed):

```bash
pip install pytest
python3 -m pytest tests/ -q
```

---

## 3. How it fits together

```
                  ┌──────────────┐
  contract  ─────▶│  extract.py  │  PDF/DOCX/TXT ──▶ one plain-text string
                  └──────┬───────┘
                         │  (this exact string is used for BOTH the API call
                         │   and the verification step — they must not diverge)
                         ▼
                  ┌──────────────┐     ┌──────────────┐
                  │ analyzer.py  │◀────│ playbook.py  │  our negotiating positions
                  └──────┬───────┘     └──────────────┘
                         │  messages.parse(output_format=ContractAnalysis)
                         │  model: claude-opus-5, adaptive thinking
                         ▼
                  ┌──────────────┐
                  │ verify_quote │  ← every quote searched for in the source
                  └──────┬───────┘
                unmatched findings discarded here
                         ▼
                  ┌──────────────┐
                  │   main.py    │  FastAPI ──▶ static/index.html
                  └──────────────┘
```

| File | Responsibility |
|---|---|
| `app/extract.py` | File → text. Rejects scanned PDFs rather than returning nothing |
| `app/playbook.py` | The 12 negotiating positions. **Edit this first** — it is the part specific to your business |
| `app/models.py` | Pydantic schemas. Passed straight to the API as the output schema |
| `app/analyzer.py` | The Claude call, and quote verification |
| `app/main.py` | HTTP endpoints |
| `static/index.html` | Single-page UI, no build step |

---

## 4. Step by step: what happens on one upload

### Step 1 — Extract text (`extract.py`)

PDFs go through `pypdf` with page markers. DOCX pulls paragraphs **and tables** —
contracts put fees, SLAs and caps in tables, so dropping them would lose the most
negotiated content in the document.

Cleanup is deliberately minimal: line endings normalised, trailing spaces
stripped, runs of blank lines collapsed. Nothing moves characters *within* a
line, because Step 4 matches against these exact offsets.

If a PDF yields almost no text it is a scan, and it is **rejected with a clear
message** rather than being reviewed as if it were empty.

### Step 2 — Build the prompt (`analyzer.py` + `playbook.py`)

The system prompt does three things:

1. Tells the model to **read the whole document before reporting**, because
   contracts qualify themselves across clauses — a cap in clause 11 is routinely
   gutted by a carve-out in clause 19.
2. Injects the **playbook** so findings are measured against our actual
   positions, not generic best practice.
3. States the evidence rule — and states it *truthfully*, which is what makes it
   work: quotes are checked, unmatched findings are deleted.

The system prompt carries `cache_control: {"type": "ephemeral"}`. It is identical
on every request, so on repeat reviews only the contract itself is charged at
full input rate.

### Step 3 — Call Claude (`analyzer.analyse`)

```python
response = client.with_options(timeout=900.0).messages.parse(
    model="claude-opus-5",
    max_tokens=32_000,
    thinking={"type": "adaptive"},
    output_config={"effort": EFFORT},
    output_format=ContractAnalysis,     # Pydantic → JSON schema, enforced
    system=[{..., "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": ...}],
)
```

Decisions worth knowing:

- **`claude-opus-5`** — ContractEval found proprietary frontier models clearly
  ahead of open-source on this exact task (CUAD clause-level risk
  identification). Legal review is not where you save on model choice.
- **1M context, no chunking.** A whole contract goes in one request. Chunking
  would destroy the cross-clause reasoning that catches the carve-out in clause
  19. Documents over the limit are **rejected, never truncated** — a review of an
  unknown subset of a contract is more dangerous than no review, because it reads
  like a complete one.
- **Adaptive thinking** — this is multi-hop reasoning over a long document.
- **`messages.parse` with a Pydantic model** — the schema is enforced by the API,
  so there is no JSON parsing or repair code anywhere in this project.
- **`stop_reason` is checked before the content is read**, covering refusals and
  truncation explicitly.

### Step 4 — Verify every quote (`analyzer.verify_quote`) ← the important one

Structured output guarantees the *shape* of the answer. It cannot make a field
true. So the schema forces every finding to carry a verbatim `quote`, and the
server then goes and looks for it in the source document:

| Tier | Match | Marked exact? |
|---|---|---|
| 1 | Character-for-character | ✅ |
| 2 | Typographic folding — curly quotes, en/em dashes, non-breaking spaces | ❌ |
| 3 | Whitespace collapsed, offsets mapped back to the original | ❌ |

Tiers 2 and 3 exist because PDF extraction inserts line breaks mid-sentence and
uses typographic characters that models normalise when quoting. They tolerate
*formatting* drift. They do **not** tolerate paraphrase.

Then:

- **Findings** with no match are **discarded**, and counted. The count is shown
  in the UI, because a silently shorter list is a lie.
- **Obligations** with no match are **kept but flagged** — a missed deadline is a
  worse outcome than an unhighlighted one.
- A `playbook_rule` ID that is not in `playbook.RULES` is **stripped**, because an
  invented rule ID implies a policy decision nobody wrote down.
- Matches return character offsets, so the UI highlights the real clause in the
  real document and you verify in one click.

This is tested as a property, not assumed:

```
tests/test_anchoring.py   paraphrase → rejected
                          invented clause → rejected
                          quote stitched from two clauses → rejected
                          quote spanning a line break → accepted, offsets valid
tests/test_pipeline.py    a hallucinated finding mixed with real ones
                          never reaches the API response
```

### Step 5 — Render

The findings list and the document sit side by side. Click any quote and the
document pane scrolls to the highlighted clause. Overlapping highlights are
de-duplicated back-to-front so offsets stay valid.

---

## 5. Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Or use `ant auth login` |
| `CONTRACT_MODEL` | `claude-opus-5` | |
| `CONTRACT_EFFORT` | `high` | `low` for cheap triage, `max` when correctness dominates |

**The playbook is the part you should actually edit.** `app/playbook.py` holds 12
rules — liability caps, indemnities, auto-renewal windows, payment terms, IP
ownership, governing law. They are currently written for a supplier-side services
business. Replace them with your own positions and nothing else needs to change.

---

## 6. API

| Endpoint | Purpose |
|---|---|
| `GET /` | The UI |
| `GET /api/health` | Model, effort, whether credentials resolved |
| `GET /api/playbook` | The active rules |
| `POST /api/analyse` | multipart file upload → full analysis JSON |

---

## 7. What this does not do

Set out plainly rather than discovered later — see `RESEARCH.md` §7 for detail.

- **No OCR** — scanned PDFs are rejected, not silently mis-reviewed.
- **Recall is unmeasured** — it verifies what it *says*, not what it *misses*.
  Building the labelled gold set (`RESEARCH.md` §6) is the next real piece of work.
- **Single document** — cannot reason across an MSA plus its SOWs and schedules.
- **No persistence, no audit log, no auth** — documents are held in memory and
  never written to disk. Production needs an immutable log of document hash,
  model, prompt version and playbook version.
- **Playbook needs a deploy to change.**
- **Before any real client contract**: executed DPA, zero-data-retention
  commitment, and a privilege call from counsel. `RESEARCH.md` §5.

---

## 8. Next steps

1. Build the gold set and measure recall — the highest-value thing available.
2. OCR for scanned PDFs.
3. Move the playbook into a database with a UI so legal can edit rules directly.
4. Multi-document review (MSA + SOWs together).
5. Audit log and authentication.
6. DOCX export of the review, for sending to the counterparty.
