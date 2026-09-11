# Zefix REST API — what we can actually retrieve

**Question asked:** can we get direct access to ownership structures through the
Zefix API, or only legal documents?

**Short answer:** neither, quite. We get **structured company master data plus
commercial-register publication records**. We do **not** get ownership
structures, and we do not get legal documents as files either — we get
publication *texts* and a *deep link* to the cantonal register extract.

---

## 1. Verdict on ownership structures

No. And this is a property of Swiss law, not a limitation of the API, so no
amount of integration work changes it:

| Layer | Public? | In the Zefix API? |
|---|---|---|
| **AG / SA shareholders** | No — never public. The share register is kept privately by the company. | No |
| **GmbH / Sàrl members** (`Gesellschafter`, with their capital quotas) | **Yes** — registered in the commercial register and public | **Not as structured data.** Only inside SOGC publication prose and the cantonal extract |
| **Officers / signatories** (board, managing officers, signature rights) | Yes | **Not as structured data.** Same route: SOGC text |
| **Beneficial owners (UBO)** | No — the new federal transparency register is explicitly **non-public** | No |

Three consequences worth being explicit about:

- **For an AG, there is no lawful public source of shareholders at all.** If the
  target entities are AGs, ownership is simply not obtainable from Swiss public
  registry data, by API or otherwise.
- **For a GmbH, ownership *is* public but not machine-readable via Zefix.**
  Getting it means parsing SOGC publication text (German/French/Italian, mixed
  within a single feed) or the cantonal extract behind `cantonalExcerptWeb`.
  That is an NLP/extraction project, not an API integration.
- **The UBO register does not help us.** Switzerland's Federal Act on the
  Transparency of Legal Entities enters into force **1 October 2026** — three
  weeks from now — with a two-year transition for initial filings. The register
  is deliberately closed: access is restricted to designated authorities and
  AML-regulated professionals. Not open to the public, journalists, or research
  users. Unless we qualify as an AML-supervised entity, it is out of reach.

## 2. What the API does give us

Base URL `https://www.zefix.admin.ch/ZefixPublicREST/api/v1`, HTTP Basic auth on
every endpoint.

| Method | Path | Returns |
|---|---|---|
| POST | `/company/search` | Name search → match stubs (name, UID, CHID, EHRAID, legal form, registry office, status) |
| GET | `/company/uid/{uid}` | Full company record |
| GET | `/company/chid/{chid}` | Same, by cantonal CH-ID |
| GET | `/company/ehraid/{id}` | Same, by EHRA surrogate key |
| GET | `/sogc/bydate/{date}` | **All** SOGC/SHAB publications for one day, across all 26 cantons |
| GET | `/legalForm`, `/community`, `/registryOffice` | Reference lists |

The company record carries: registered name, UID/CHID/EHRAID, legal form,
status, registered address and canton, **purpose** (`purpose` — free text),
**nominal capital**, name history, language translations, branch offices,
merger/takeover lineage (`hasTakenOver` / `wasTakenOverBy`), a
`cantonalExcerptWeb` link, and `sogcPub[]` — the publication history.

Two things here are genuinely useful and easy to miss:

- **`/sogc/bydate/{date}` is a national delta feed.** Poll it daily and you get
  every register mutation in Switzerland — incorporations, liquidations, capital
  changes, officer changes, address moves — as they happen. That is the single
  highest-value endpoint for building a maintained dataset.
- **Merger lineage is structured.** `hasTakenOver` / `wasTakenOverBy` give real
  corporate-event edges. This is the closest thing to a structured *corporate
  relationship* graph the API offers — but it is succession, not ownership. It
  will not tell you that A holds 60% of B.

## 3. What "legal documents" really means here

Not documents. `sogcPub[]` entries give publication metadata (SOGC id, date,
registry office, mutation type) and the **publication message text**. The actual
PDF extract lives in the cantonal register portal, reachable through the
`cantonalExcerptWeb` URL — a per-canton web portal, not a document API. There is
no bulk document endpoint and no uniform PDF fetch across the 26 cantons.

So a pipeline that wants documents ends at a link, per company, per canton.

## 4. Recommendation

1. **Use Zefix for what it is good at**: entity resolution and master data. UID
   is a clean national join key — it links to VAT, and to the BFS business
   register. For deduplicating or enriching an entity list, this is excellent.
2. **Poll `/sogc/bydate/` daily** if we want current data rather than snapshots.
3. **Do not scope any deliverable around ownership** without first deciding
   whether the targets are GmbH or AG. GmbH ownership is an extraction problem
   with real but bounded accuracy; AG ownership is impossible.
4. **If ownership is genuinely the requirement**, the honest options are
   commercial providers who have already done the SOGC parsing (Moneyhouse,
   CompanyData, Kyckr and similar), or annual-report disclosures for listed
   companies — not this API.

## 5. Access, limits, licence

- Credentials are **not self-service**. Request them from the EHRA at the
  Federal Office of Justice (`zefix@bj.admin.ch`). Basic auth, mandatory on
  every endpoint.
- A **TEST environment** exists at `https://www.zefixintg.admin.ch/...` — use it
  while developing.
- The service is **rate limited**; the client in this folder throttles to one
  request per 0.5 s by default and backs off on HTTP 429.
- The authoritative schema is the OpenAPI document at
  `/ZefixPublicREST/v3/api-docs`. `explore.py --spec` downloads it.

## 6. Verification status — please read

This analysis is from the official API documentation, the OpenAPI surface as
described by maintained third-party clients, and Swiss commercial-register law.
**No live calls were made**, for two reasons: this sandbox's network policy
blocks `admin.ch` outright, and the API needs EHRA credentials we do not hold.

So the code in this folder is written to be *verified by whoever has
credentials*, not asserted as already verified:

- `explore.py --probe` reports the real HTTP status of every candidate endpoint,
  so unverified paths (`/legalForm`, `/community`, `/registryOffice`, flagged in
  `ENDPOINTS`) get confirmed or corrected on first contact.
- `explore.py --company <UID>` prints a **complete field inventory** of the live
  response and explicitly flags any field name that looks like ownership,
  shareholder, person or mandate data.

If that probe ever flags an ownership field, section 1 is wrong and should be
rewritten. That is the intended test.

Offline, the parsing and request logic is covered by 11 unit tests against
fixtures (`python -m unittest discover -s . -p "test_zefix*.py"`), including a
guard proving the ownership detector is not silently returning "nothing found".

## Sources

- [Zefix Swagger UI](https://www.zefix.admin.ch/ZefixPublicREST/swagger-ui/index.html?configUrl=%2FZefixPublicREST%2Fv3%2Fapi-docs%2Fswagger-config) · [Zefix REST API on I14Y](https://www.i14y.admin.ch/en/catalog/dataservices/6ef8f5d2-3d6a-4d84-bf60-5e65fde98a87) · [Zefix on opendata.swiss](https://opendata.swiss/en/dataset/zefix-zentraler-firmenindex)
- Client implementations: [TenderLift/zefix-client](https://github.com/TenderLift/zefix-client) · [validitylabs/zefix](https://github.com/validitylabs/zefix) · [jschwendener/zefix-php](https://github.com/jschwendener/zefix-php)
- Ownership/registry practice: [Kyckr — Switzerland's Company Register](https://www.kyckr.com/blog/swiss-company-registry) · [Comsure — guide to Switzerland's Company Register](https://www.comsuregroup.com/news/a-guide-to-switzerland-s-company-register-2026-update/) · [Topograph — Switzerland](https://docs.topograph.co/essentials/switzerland) (on SOGC text parsing for legal representatives)
- UBO register: [CMS — reporting to the beneficial ownership register from 1 October 2026](https://cms.law/en/che/legal-updates/new-obligations-in-switzerland-to-report-to-centralised-electronic-register-of-beneficial-ownership-effective-1-october-2026) · [EBCO — Switzerland's new beneficial ownership register](https://www.ebco.ch/transparency-everywhere-switzerlands-new-beneficial-ownership-register-and-the-global-reporting-convergence/)
