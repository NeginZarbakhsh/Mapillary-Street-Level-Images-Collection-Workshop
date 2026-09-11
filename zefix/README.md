# Zefix REST API exploration

A small toolkit for probing the [Zefix](https://www.zefix.admin.ch) Public REST
API — the Swiss commercial register (Zentraler Firmenindex), run by the EHRA at
the Federal Office of Justice.

**Start with [`FINDINGS.md`](FINDINGS.md)** — it answers what the API can and
cannot give us, in particular the ownership-structure question. The code here
exists so that answer can be re-checked against the live service.

> Note: this folder is self-contained and unrelated to the Mapillary workshop
> material in the repository root.

## Getting credentials

Basic authentication is mandatory on every endpoint and there is no
self-service signup. Request credentials from the EHRA: **zefix@bj.admin.ch**.

A test environment is available at `https://www.zefixintg.admin.ch/` — develop
against it with the `--test` flag.

## Usage

No dependencies; standard library only, Python 3.9+.

```bash
export ZEFIX_USER=...
export ZEFIX_PASSWORD=...

python zefix/explore.py --spec                  # download the OpenAPI document
python zefix/explore.py --probe                 # HTTP status of every endpoint
python zefix/explore.py --company CHE-105.805.185
python zefix/explore.py --search "Migros" --canton ZH
python zefix/explore.py --sogc 2026-09-10       # a day of register mutations
python zefix/explore.py --all --out out/        # everything, raw JSON to out/
```

`--company` and `--search` print a full **field inventory** of the response and
flag any field name resembling ownership, shareholder, person or mandate data.
That flagging is the point: if it ever fires, `FINDINGS.md` needs rewriting.

## As a library

```python
from zefix.zefix_client import ZefixClient

client = ZefixClient(username="...", password="...")
record = client.company_by_uid("CHE-105.805.185")
print(record["name"], record["legalForm"], record["cantonalExcerptWeb"])

for pub in record.get("sogcPub", []):
    print(pub["sogcDate"], pub["message"][:120])
```

The client throttles to one request per 0.5 s (`min_interval`), retries 429 and
5xx with exponential backoff, and fails fast on 401/403/404.

## Tests

Offline, no credentials or network needed — `urlopen` is patched and the
payloads are fixtures:

```bash
python -m unittest discover -s . -p "test_zefix*.py" -v
```

## Files

| File | Purpose |
|---|---|
| `FINDINGS.md` | The analysis: what is retrievable, and the ownership verdict |
| `zefix_client.py` | API client — all endpoints, auth, throttling, retries |
| `explore.py` | CLI probe: endpoint discovery and field inventory |
| `test_zefix_client.py` | Offline unit tests against fixtures |
