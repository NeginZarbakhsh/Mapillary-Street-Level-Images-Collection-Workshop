# Legal contract analysis: research and design rationale

Prepared for the solution review. This is the "go and research it" half of the
brief; `README.md` is the "how the prototype works" half.

> **Caveat on sourcing.** Vendor pricing and positioning in this market change
> every few months. Everything in §2 was checked against public sources in August
> 2026 and should be re-verified before it goes in front of a client. Figures
> attributed to vendors are their own claims unless stated otherwise.

---

## 1. What problem is actually being solved

"Contract analysis" is three different jobs that get bundled under one name, and
choosing between them is the first decision, not a detail:

| Job | Question it answers | Who asks | Failure mode |
|---|---|---|---|
| **Triage / review** | "Is this safe to sign, and what do I push back on?" | Commercial, sales, procurement | Missing a risk |
| **Extraction / abstraction** | "What are the renewal dates across our 4,000 live contracts?" | Legal ops, finance | Wrong values at scale |
| **Drafting / redlining** | "Rewrite clause 7 to our standard position" | Lawyers | Bad drafting shipped |

They need different architectures. Triage is a single-document, high-recall,
explain-yourself problem. Extraction is a batch, high-precision, schema-stable
problem where you would use the Batch API at 50% cost and accept latency.
Redlining needs the drafting surface the lawyer already works in — practically,
a Word add-in.

**The prototype targets triage**, because it is the job with the widest audience
(non-lawyers who have to make a signing decision), the clearest measurable win,
and the lowest blast radius if it is wrong — a human still signs.

---

## 2. Market landscape

The legal AI market is projected to pass **$37bn in 2026**, and corporate legal
AI adoption reportedly **more than doubled in a year, from 23% to 52%**. This is
a crowded, well-funded category, not a greenfield one.

| Vendor | Shape | Reported positioning |
|---|---|---|
| **Harvey** | Broad legal AI assistant (research, drafting, review) | Enterprise/elite firms. March 2026 raise valued it at ~$11bn |
| **Luminance** | End-to-end review and negotiation, "Legal-Grade AI" | Enterprise M&A and due diligence; quote-based pricing, mid-size deployments widely reported in the five-to-six-figure range annually |
| **Robin AI** | Playbook-based redlining automation | Small/mid-size firms; tiers reported from ~$5k/yr, enterprise ~$40–80k |
| **Spellbook** | Microsoft Word add-in, in-line review and drafting | Meets lawyers in the tool they already use |
| **Ironclad / Icertis / LinkSquares / Juro** | CLM platforms with AI bolted on | Own the contract lifecycle, not just review |
| **Thomson Reuters CoCounsel / LexisNexis Lexis+ AI** | Incumbent research platforms extended into drafting | Distribution advantage |

Two structural observations:

1. **The category is moving from "review assistant" to agentic workflows.**
   A&O Shearman and Harvey launched agents in 2025 handling antitrust filing
   analysis and loan document review; Luminance claims fully autonomous contract
   negotiation. Anything we build should assume the "summarise this document"
   surface is commoditised within a year.
2. **The defensible layer is the playbook, not the model.** Every vendor has
   access to comparable frontier models. What differentiates is the encoded set
   of positions a specific business takes, and the workflow it plugs into.

### Build vs buy

| | Buy | Build |
|---|---|---|
| Time to value | Weeks | Months |
| Cost | $5k–$80k+/yr, per-seat | Engineering time + ~cents per contract |
| Fit to our playbook | Configurable within their model | Exact |
| Data control | Their DPA, their subprocessors | Ours |
| Differentiation | None — competitors buy the same thing | Ours |

**Recommendation: buy for general legal work, build for the narrow, repeated,
high-volume review that is specific to us.** A team reviewing the same five
contract types against the same playbook every week is the build case; that is
worth roughly a week of engineering, and the marginal cost per contract is
cents. Ad-hoc review of unfamiliar instruments is the buy case.

---

## 3. Technical approaches, and why LLM won

**Generation 1 — rules and regex.** Deterministic, auditable, cheap. Breaks on
the first contract that says "the Supplier's liability shall in no event be
limited" instead of matching your pattern. Contracts are adversarially drafted;
pattern matching loses.

**Generation 2 — supervised clause classification.** The Kira/Seal/eBrevia
approach: train a classifier per clause type on labelled contracts. Genuinely
good at "find the assignment clause" and still competitive on pure extraction.
Costs thousands of labelled examples per clause type, and cannot answer "why is
this bad *for us*".

**Generation 3 — LLM.** Handles unseen phrasing, gives reasoning, needs no
training data, and takes the playbook as plain text. This is what makes
per-customer playbooks economically viable — encoding a new rule is writing a
sentence, not labelling a corpus.

The relevant academic benchmark is **CUAD** (Contract Understanding Atticus
Dataset): 510+ commercial contracts, 13,000+ expert annotations, 41 clause
categories. The 2025 **ContractEval** benchmark evaluated 4 proprietary and 15
open-source LLMs on CUAD across correctness (F1), output accuracy (Jaccard
similarity) and "laziness" — the rate of incorrectly answering "no related
clause". Findings worth carrying into our design:

- **Proprietary models outperformed open-source** on both correctness and
  output effectiveness. This is an argument against self-hosting a small model
  for this task today.
- **"Laziness" is a real, measured failure mode.** Models under-report. Recall,
  not precision, is the metric to optimise for in triage — a missed uncapped
  indemnity costs far more than a false positive a human dismisses in ten
  seconds.
- ContractEval scored a span as correct at **Jaccard ≥ 0.15** against the ground
  truth span, precisely because LLM output boundaries wander. Our evaluation
  should score on *clause identified*, not string equality.

---

## 4. The hallucination problem — and why this design is shaped the way it is

This is the part that decides whether the tool is usable in a legal context.

The Stanford RegLab study **"Hallucination-Free? Assessing the Reliability of
Leading AI Legal Research Tools"** tested purpose-built legal AI products across
202 legal queries with expert hand-scoring. It found that **LexisNexis Lexis+ AI
and Thomson Reuters' tools hallucinated between 17% and 33% of the time** —
better than a general chatbot, but nowhere near the "hallucination-free" claims
the vendors were making. Retrieval-augmented generation reduced the problem; it
did not eliminate it.

The study's most important finding for us is not the headline rate. It is
**misgrounding**: the system states the law correctly, cites a real source that
genuinely exists, and *that source does not support the claim*. This is worse
than a fabricated citation, because a fabricated citation fails the first check
a reviewer performs, and a misgrounded one passes it.

Applied to contract review, the misgrounding failure looks like: *"Clause 11.3
caps your liability at 12 months' fees"* — where clause 11.3 exists, is about
liability, and says something different. A busy reviewer sees a clause number,
sees it is about liability, and moves on.

### The four mitigations, and which we use

| Mitigation | What it does | Verdict |
|---|---|---|
| Better prompting | Reduces rate | Necessary, insufficient — cannot be verified |
| RAG over a clause library | Grounds in retrieved text | Wrong tool here: the source document *is* the context, and Opus 5's 1M window fits a whole contract. Chunking a contract actively destroys the cross-clause reasoning that matters most |
| Structured output | Guarantees response *shape* | Necessary, insufficient — a schema cannot make a field true |
| **Verified quote anchoring** | Guarantees every claim is attached to text that provably exists | **This is the design** |

**Quote anchoring** is the core engineering decision in this prototype:

1. The schema **requires** every finding and obligation to carry a `quote` field
   containing verbatim contract text.
2. The system prompt tells the model, truthfully, that the quote is checked and
   the finding is deleted if it does not match — so the cheapest way to be
   reported is to be real.
3. After the response returns, the server **searches the source document** for
   each quote (`analyzer.verify_quote`), tolerating whitespace and typographic
   drift but *not* paraphrase.
4. Findings whose quotes cannot be located are **discarded before the user sees
   them**, and the discard count is displayed.
5. Surviving findings carry character offsets, so the UI highlights the actual
   clause in the actual document. The reviewer verifies in one click.

This converts an unfalsifiable claim into a falsifiable one. It does not make
the model's *judgement* correct — a real quote can still carry a wrong risk
assessment — but it eliminates the fabricated-clause and misgrounded-clause
failure modes entirely, which is the class of error that makes legal AI
unusable. `tests/test_anchoring.py` and `tests/test_pipeline.py` encode this as
a tested property: paraphrases, invented clauses, and quotes stitched from two
locations are all rejected.

**What it does not do:** it cannot catch a *missed* risk. Recall remains the
open problem, and is why §6 proposes a labelled regression set.

---

## 5. Data protection, privilege and confidentiality

Non-negotiable before this touches a real client contract.

- **GDPR Art. 28** requires a signed **DPA** with any processor handling
  personal data on your behalf. Contracts are dense with personal data —
  signatories, contacts, registered addresses. Anthropic offers a DPA; it must
  be executed before production use.
- **Zero Data Retention.** The commitment to obtain is that inputs and outputs
  are not retained and are not used to train, fine-tune, evaluate or benchmark
  any model. Ask the question in exactly that form, and ask for the contract.
- **Legal professional privilege.** The live risk is **inadvertent waiver** by
  routing privileged material through an external system. This is a question for
  counsel, not engineering, and it must be asked *before* deployment, not after.
  Practically: keep privileged material out of scope for v1, or get written
  sign-off that the DPA plus ZDR posture preserves privilege.
- **Data residency.** `inference_geo` can pin where inference runs, which
  matters for EU-only commitments.
- **Retention on our side.** The prototype holds documents in memory only and
  writes nothing to disk. That is a deliberate default, and the moment anyone
  adds a database it becomes a new DPIA question.
- **Audit trail.** Any production version needs an immutable log of document
  hash, model ID, prompt version, playbook version, and response — so a review
  can be reconstructed months later. Not in the prototype; called out in §7.

---

## 6. Evaluation

"It looked good on three contracts" is not evidence. The proposed method:

1. **Build a gold set.** 30–50 contracts across the types we actually see, each
   reviewed by a human who records every issue they would raise, with the clause
   text. This is a week of someone's time and it is the single highest-value
   thing in this whole project.
2. **Score recall first.** Of the issues the human found, what fraction did the
   system find? Target recall over precision — see the ContractEval "laziness"
   finding.
3. **Score precision second.** Of what the system reported, what fraction did
   the human agree was worth raising?
4. **Track the anchor discard rate.** A rising rate of discarded findings is an
   early warning that a prompt or model change has degraded grounding.
5. **Match on clause identity, not string equality**, per ContractEval's
   Jaccard ≥ 0.15 convention.
6. **Re-run on every prompt, playbook or model change.** Prompt edits are code
   changes and deserve the same regression discipline.

---

## 7. Known limitations of the prototype

Stated plainly so nobody oversells it:

- **No OCR.** Scanned PDFs are rejected with a clear message rather than
  silently returning an empty review. Adding OCR is the single biggest coverage
  win available.
- **Recall is unmeasured.** Until the gold set in §6 exists, we do not know what
  it misses.
- **Single document.** It cannot reason across an MSA and its SOWs, order forms
  and schedules — which is where a large share of real risk lives.
- **No persistence, no audit log, no auth.** It is a prototype, not a service.
- **Playbook is code.** Business users cannot edit rules without a deploy.
- **English-language commercial contracts only**, tested against common law
  drafting conventions.
- **It is not legal advice**, and every output should carry that statement.

---

## 8. Recommendation

Ship the triage prototype into a **human-in-the-loop pilot** with one team and
one contract type. Do not automate any decision. Measure recall against a gold
set before widening scope. Treat the playbook — not the model — as the asset
worth investing in, because it is the only part a competitor cannot buy.

---

## Sources

- [Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools — Stanford RegLab](https://reglab.stanford.edu/publications/hallucination-free-assessing-the-reliability-of-leading-ai-legal-research-tools/) ([paper](https://arxiv.org/pdf/2405.20362))
- [Stanford Will Augment Its Study Finding that AI Legal Research Tools Hallucinate in 17% of Queries — LawSites](https://www.lawnext.com/2024/05/stanford-will-augment-its-study-finding-that-ai-legal-research-tools-hallucinate-in-17-of-queries-as-some-raise-questions-about-the-results.html)
- [ContractEval: Benchmarking LLMs for Clause-Level Legal Risk Identification in Commercial Contracts](https://arxiv.org/abs/2508.03080)
- [Contract Understanding Atticus Dataset (CUAD) v1](https://simulation.hash.ai/@atticusproject/cuad)
- [Best AI Contract Review Tools for Law Firms in 2026 — Layer3 Labs](https://www.layer3labs.io/comparisons/best-ai-contract-review-tools-for-law-firms)
- [The Best AI Tools for Legal Teams and Contract Review: An Honest 2026 Buyer Comparison — Superkind](https://superkind.ai/blog/ai-legal-tools)
- [What Is Robin AI? Contract Review Guide (2026) — Layer3 Labs](https://www.layer3labs.io/guides/robin-ai-explained)
- [Data processing: legal professional privilege (LPP) and client confidentiality — The Law Society](https://www.lawsociety.org.uk/topics/gdpr/lpp-and-client-confidentiality)
- [Data Security in Legal AI: What to Know Before You Sign — GC AI](https://gc.ai/blog/data-security-ai-legal-tech)
