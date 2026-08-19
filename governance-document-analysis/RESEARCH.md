# Azure AI Search vs. MongoDB Atlas vs. custom retrieval

This is the research assigned to Negin in the meeting: pricing, architecture
and suitability for Azure AI Search and MongoDB Atlas, compared against each
other and against the custom retrieval pipeline Mahdie already has running.

> Pricing figures below were checked against public sources in August 2026 and
> should be re-verified with Azure/MongoDB's own calculators before a
> commitment — cloud pricing moves. Where a source is a review site rather
> than the vendor's own docs, that's noted.

---

## 0. The question underneath the question

The meeting spent most of its time comparing *how* to do retrieval-augmented
generation, without first asking *whether this document needs RAG at all*.
That's worth answering first, because it changes what the pricing comparison
is even for.

RAG (chunk → embed → store → retrieve → generate) earns its cost when either:

1. **You're searching across many documents to find the right one** (or the
   right section of one very long one) — a corpus-scale retrieval problem.
2. **A single document is too large to fit in the model's context window.**

Neither is true for the working example from the meeting. The sample statute
is **10 pages, ~20,000 characters, ~5,000 tokens**. Claude Opus 5 has a
**1,000,000-token context window** — the whole document is under 1% of it.
Chunking and retrieving from a 10-page document doesn't make the answer
cheaper or better; it adds a failure mode that doesn't otherwise exist: **a
retrieval miss**. If the relevant clause isn't in the top-k chunks returned by
the vector search, the model never sees it and answers confidently wrong,
silently. Whole-document mode cannot have that failure — the model reads
everything, every time.

**This prototype defaults to whole-document mode and only switches to
retrieval when a document (or a question spanning a corpus) is actually too
large.** See `README.md` for the exact threshold and how it's decided. The
comparison below still matters — the moment the real pilot is "search across
our full archive of association bylaws to find which ones grant a member
enhanced voting rights," that's a genuine corpus-retrieval problem, and one of
these platforms becomes the right layer for it.

---

## 1. Azure AI Search

**What it is.** A managed *search engine*, not a database. You define an
index, it stores your content plus vectors, and serves keyword, vector, or
hybrid (keyword + vector, merged with reciprocal rank fusion) queries, with an
optional semantic reranking pass on top.

**Pricing model.** Two modes:

- **Serverless** — scales automatically, no replica/partition sizing. Newer,
  simplest to reason about for a variable or unpredictable workload.
- **Dedicated tiers**, billed by **search units (SUs) = replicas × partitions**,
  charged hourly regardless of query volume:

| Tier | Reported cost | Notes |
|---|---|---|
| Free | $0 | Shared capacity, tiny quota, fine for a first look only |
| Basic | ~$75/mo (~$0.10/hr) | Up to 3 replicas, **1 GB vector storage quota** |
| Standard S1 | ~$250/mo (~$0.34/hr) | Default production tier, scalable partitions/replicas |
| Standard S2 / S3 | Higher, up to ~$1.39/hr for S3 | Larger indexes, more throughput |
| Storage Optimized L1/L2 | Lower $/TB than Standard | Large, low-churn indexes |

Cost is a **fixed hourly rate for provisioned capacity**, independent of how
many documents you actually have or how often you query — you pay for the
tier around the clock once it's provisioned. ([Microsoft Learn: pricing model and tiers](https://learn.microsoft.com/en-us/azure/search/search-sku-tier), [Microsoft Learn: service limits](https://docs.azure.cn/en-us/search/search-limits-quotas-capacity))

**Strengths.** Best-in-class hybrid retrieval and reranking — this matters
when relevance quality across a large, heterogeneous corpus is the actual
bottleneck. Mature filtering, faceting, and enterprise search features if the
product ever needs to be "search," not just "retrieval for one RAG call."

**Weakness for this use case.** The Basic tier's cost exists whether you have
5 documents or 5,000 — it's provisioned capacity, not consumption. For a pilot
measured in tens of documents, you're paying $75+/month for a search engine
sized for a workload you don't have yet.

---

## 2. MongoDB Atlas Vector Search

**What it is.** Vector search *inside* MongoDB — you store chunks and their
embeddings as ordinary documents in a collection, define a vector index, and
query with `$vectorSearch` (approximate nearest-neighbour, HNSW-indexed) as an
aggregation stage. It is a feature of a general-purpose database, not a
dedicated search product.

**Pricing model.**

- **M0 (free tier)** supports Atlas Vector Search — a real, working setup at
  **$0 cost**, sufficient to prototype and validate an approach before
  committing to anything. ([Modern DataTools: Atlas Vector Search pricing](https://www.modern-datatools.com/tools/mongodb-atlas-vector-search/pricing))
- **Dedicated tiers (M10+)** start around **$0.08/hr (~$57/mo)** for compute,
  but a production replica set has **3 nodes by default**, so the realistic
  cost is roughly 3× the single-node figure — an M10 "$57/mo" deployment is
  closer to **$170/mo** running properly. ([CloudZero: MongoDB pricing guide](https://www.cloudzero.com/blog/mongodb-pricing/))
- **Dedicated Search Nodes** (optional, isolate search compute from database
  compute at scale) range **$0.12/hr (S20) to $1.77/hr (S60)**, minimum two
  for high availability — but these are only needed once query volume is high
  enough to contend with normal database load; a pilot runs vector search on
  the regular cluster nodes at no extra charge. ([Modern DataTools](https://www.modern-datatools.com/tools/mongodb-atlas-vector-search/pricing))
- HNSW indexes hold roughly **1.2–1.5× the raw vector data size in RAM** —
  relevant for sizing the cluster tier, not a separate cost line.

**Strengths.** Genuinely free to prototype at real scale. If application data
(members, votes, board composition — the structured facts this whole project
is trying to extract) already lives in or would naturally live in MongoDB,
vectors sit next to it in one system — no second service, no sync problem.
Simpler operational surface than running a dedicated search engine alongside a
database.

**Weakness.** Retrieval features are a step behind a dedicated search
engine — hybrid search and hybrid hybrid-plus-rerank workflows are less mature
than Azure AI Search's. Fine for "find the top-k relevant chunks for a RAG
call"; not the tool of choice if the product's core value becomes enterprise
full-text search.

---

## 3. Custom / self-hosted (what Mahdie already has)

Chunk, embed, and compare vectors yourself — in memory for a small corpus, or
in Postgres with `pgvector` for something durable, no managed service at all.
This is architecturally what `LocalVectorStore` in this prototype does: a
brute-force cosine-similarity scan.

**Cost.** Effectively zero beyond the compute you're already running.
Embedding API calls are the only real marginal cost, and they're the same
whichever store holds the results.

**When it stops being enough.** A brute-force scan is O(n) per query. At a
few thousand chunks — call it a few hundred documents — that's still
millisecond-scale and genuinely fine. It becomes the wrong tool when either
the corpus reaches tens of thousands of chunks (a real approximate-nearest-
neighbour index starts winning), or multiple people need concurrent access to
the same index with durability guarantees a Python process in memory doesn't
give you.

---

## 4. Cost comparison at pilot scale

The meeting's own conclusion was "start small." Here's what that looks like
priced out, assuming the pilot the action items describe — tens of documents,
low query volume, one or two people testing:

| Approach | Monthly cost at pilot scale | Setup effort | Ceiling before you'd need to move |
|---|---|---|---|
| **Custom / local** (this prototype's default) | **$0** | Already built | Low thousands of chunks, single-process |
| **MongoDB Atlas M0** | **$0** | ~1 hour (cluster + index) | Free-tier storage/connection limits |
| **MongoDB Atlas M10 + Vector Search** | **~$170/mo** (3-node) | ~1 day | Scales with cluster tier |
| **Azure AI Search Basic** | **~$75/mo** | ~1 day (index schema, ingestion pipeline) | 1 GB vector quota, then Standard tier |
| **Azure AI Search Standard S1** | **~$250/mo** | ~1–2 days | Scales with SUs |

**At the volumes this pilot is actually operating at, every managed option
costs money to buy headroom nobody is using yet.** That's the concrete version
of the meeting's own "a managed service should only be used if it provides
meaningful value relative to its cost" position.

---

## 5. Recommendation

1. **Stay on custom/local retrieval for the pilot.** It's free, it's already
   built (both Mahdie's prototype and this one), and at tens-to-low-hundreds
   of documents there is no latency or relevance problem it's failing to
   solve. This matches the meeting's "start small" and "cost matters"
   conclusions exactly.
2. **Prefer whole-document mode over retrieval wherever the document fits.**
   This is a bigger lever than the choice of vector store — it removes an
   entire failure mode (retrieval miss) rather than optimising around it.
   See §0.
3. **If the corpus outgrows a single process, reach for MongoDB Atlas next,
   not Azure AI Search.** The free M0 tier lets you prove the approach at zero
   cost before paying anything, dedicated-tier pricing is lower at comparable
   capacity, and if any operational/member data ends up in a database anyway,
   MongoDB keeps vectors and records in one system.
4. **Reach for Azure AI Search only if the requirement changes** from "answer
   questions about one document at a time" to "search relevance across a
   large, heterogeneous archive is the product" — hybrid search and semantic
   reranking are where it genuinely leads. That is not the requirement
   described in the meeting today.
5. **Revisit this decision when you have a number, not a guess** — actual
   chunk count, actual query volume, actual concurrent users. All of the above
   is priced for "tens of documents, low query volume" because that's what
   "start small" means; the right answer changes at real scale, and should be
   re-decided with real numbers rather than re-argued from first principles.

---

## 6. Working the meeting's own example

The meeting's worked example asked: *"Wie viele Stimmen hat eine einzelne
natürliche Person maximal?"* (What is the maximum number of votes a single
natural person can hold?), against `§ 12 Stimmrecht` of the sample statute,
and flagged its own draft answer — `max_votes_single_person: 3` — as
unsupported by the text.

Running that clause through this prototype's extraction schema (see
`README.md` for the mechanism) gives the honest answer, worked by hand here
since it doesn't need a model call to verify against the actual clause text:

**§ 12 Stimmrecht allocates votes by role, not by person:**

| Role | Votes | How cast |
|---|---|---|
| Ortsgruppe (local group) | 1 per started 5 eligible members | Block vote by an authorised representative |
| Bezirksgruppe (district group) | 1 | — |
| Einzelmitglieder (individual members) | 1 per started 5 present | — |
| Each Landesvorstand (state board) member | 1 | Individually |
| Bayerische Jungbauernschaft e.V. | 2 | Block vote by an authorised representative |
| Representative of another cooperative association | 1 | — |

Vote transfer ("Stimmübertragung") is explicitly permitted only for the
Ortsgruppe and Bayerische Jungbauernschaft block votes.

**The correct answer to "maximum votes for one natural person" is
`basis: undetermined`, not a number.** The statute never states whether one
person can simultaneously hold multiple qualifying roles — e.g. a Landesvorstand
seat *and* the Ortsgruppe's authorised block-vote mandate *and* representing
Bayerische Jungbauernschaft. Nothing in the document forbids stacking roles;
nothing confirms it's allowed either. A number here — 1, 3, or anything else —
would be exactly the failure the meeting caught in its own draft: a
plausible-sounding answer with no clause behind it. The honest output is the
table above, plus a note that resolving "can roles be combined" requires
either an explicit statute provision this document doesn't contain, or a
ruling from whoever administers the Verein.

This is the concrete case for the grounding mechanism in `README.md`: the
schema forces exactly this distinction (`stated` / `inferable` /
`undetermined`) instead of letting a plausible number through unchallenged.

---

## Sources

- [Azure AI Search: choose a pricing model and service tier — Microsoft Learn](https://learn.microsoft.com/en-us/azure/search/search-sku-tier)
- [Azure AI Search: service limits and quotas](https://docs.azure.cn/en-us/search/search-limits-quotas-capacity)
- [MongoDB Atlas Vector Search: pricing, plans, cost — Modern DataTools](https://www.modern-datatools.com/tools/mongodb-atlas-vector-search/pricing)
- [MongoDB Pricing Explained — CloudZero (2026)](https://www.cloudzero.com/blog/mongodb-pricing/)
- [Voyage AI pricing](https://docs.voyageai.com/docs/pricing) — embedding cost reference for the RAG path
