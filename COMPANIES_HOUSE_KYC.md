# Companies House KYC/KYB Lookup Tool

`companies_house_kyc.py` pulls company profile, officers and PSC (beneficial ownership)
data from the UK Companies House API, optionally screens the people it finds against the
UK Sanctions List, and writes the result out as **JSON, CSV, XLSX, Markdown or plain text**.

## Setup

```bash
pip install -r requirements-kyc.txt

# Create a REST API key at https://developer.company-information.service.gov.uk
cp .env.example .env        # then paste your key into .env
# or:
export COMPANIES_HOUSE_API_KEY=your-key
```

`.env` is gitignored. **Never commit a key, paste one into a chat, or screenshot it** —
a key that has been shared is compromised and should be deleted and regenerated in the
Companies House developer hub.

## Usage

```bash
# One company, JSON (default)
python companies_house_kyc.py 00102498

# Several companies, several formats at once
python companies_house_kyc.py 00102498 09659799 --format json,csv,xlsx

# Every format, into a chosen folder
python companies_house_kyc.py 00102498 --format all --outdir reports

# Find the number when you only have a name
python companies_house_kyc.py --search "monzo bank"

# Skip sanctions screening (faster, no 20 MB download)
python companies_house_kyc.py 00102498 --no-sanctions
```

### Options

| Option | Purpose |
| --- | --- |
| `--format` | `json`, `csv`, `xlsx`, `md`, `txt`, comma-separated, or `all` |
| `--outdir` | Output directory (default `kyb_output`) |
| `--stem` | Base filename (default: auto, with a timestamp) |
| `--api-key` | Key on the command line, if you'd rather not use env/`.env` |
| `--no-sanctions` | Skip sanctions screening |
| `--sanctions-file` | Screen against a locally downloaded UK Sanctions List XML |
| `--threshold` | Fuzzy-match threshold, 0–1 (default `0.85`) |
| `--no-filings` | Skip the filing-history lookup |
| `--print` | Also print the JSON to stdout |

## What each format gives you

| Format | Files | Best for |
| --- | --- | --- |
| `json` | `<stem>.json` | Full nested record — feeding another system |
| `csv` | `<stem>_companies.csv`, `<stem>_people.csv`, `<stem>_filings.csv` | Excel, pandas, BI tools |
| `xlsx` | `<stem>.xlsx` with `Companies` / `People` / `Filings` sheets | Sharing one file with a reviewer |
| `md` | `<stem>.md` | Pasting into a report or ticket |
| `txt` | `<stem>.txt` | Quick read in a terminal |

CSVs are written as UTF-8 with a BOM so Excel renders accented names correctly.
XLSX uses `openpyxl` when installed and falls back to a built-in standard-library
writer when it isn't, so `--format xlsx` never fails for a missing dependency.

## Use as a library

```python
from companies_house_kyc import load_api_key, make_session, run_kyb_check, load_uksl_names, screen_kyb_result, export

session = make_session(load_api_key())
result = run_kyb_check(session, "00102498")
screen_kyb_result(result, load_uksl_names())
export([result], ["json", "xlsx"], outdir="reports")
```

## Sanctions screening — read this before relying on it

Screening uses fuzzy string matching (`difflib`) against the UK Sanctions List published
by the FCDO, which has been the single official source for UK designations since
28 January 2026 (it replaced the OFSI Consolidated List). It is free and needs no key.

This is a **starting point, not production-grade screening**. Real screening needs
phonetic matching, alias and transliteration handling, and date-of-birth corroboration —
e.g. `rapidfuzz` plus a proper name-matching library. Treat hits as leads to review by
hand, and treat the absence of hits as no evidence either way. It also covers UK
sanctions only: no EU, OFAC/US, or UN lists, and no PEP or adverse-media screening.

## Data notes

- Companies House allows **600 requests per 5 minutes**; the tool backs off and retries on 429.
- Officer and PSC lists are paginated automatically, so companies with many directors are complete.
- Dates of birth are month/year only — that is all Companies House publishes.
- Companies with no PSC register return an empty `beneficial_owners` list rather than failing.

## Getting many companies, not one

There is **no endpoint that lists every UK company**. Run `python companies_house_kyc.py --bulk-info`
for the summary, or read on.

### 1. Filtered bulk listing (`--advanced-search`)

The closest the API gets: every company matching a filter. At least one filter is required.

```bash
# All active software companies (SIC 62012) -- directory only, one row each
python companies_house_kyc.py --advanced-search --sic 62012 --status active --format csv

# Everything registered in Manchester, first 500
python companies_house_kyc.py --advanced-search --location manchester --limit 500

# Incorporated in 2025, name contains "capital"
python companies_house_kyc.py --advanced-search --name-includes capital \
    --incorporated-from 2025-01-01 --incorporated-to 2025-12-31

# Add --full to also pull officers, PSC and sanctions screening for every hit (slow)
python companies_house_kyc.py --advanced-search --sic 64191 --status active --limit 100 --full
```

Filters: `--sic`, `--status`, `--type`, `--location`, `--name-includes`, `--name-excludes`,
`--incorporated-from/-to`, `--dissolved-from/-to`, `--limit`. The repeatable ones
(`--sic`, `--status`, `--type`) can be given more than once.

Without `--full` you get a directory (one row per company, one request per 100 companies).
With `--full` each company costs ~4 requests, so the tool paces itself and prints an estimate.

Companies House caps how deep pagination can go, so a very broad filter returns the first
few thousand rather than the true total — the run prints the real `hits` count. Split large
sets by SIC code or by year to work through them.

### 2. The complete register — bulk snapshot

For genuinely *all* companies, download the free **Company Data Product**: a monthly CSV
snapshot of every live company (several million rows, ~400 MB zipped, no key needed) from
<http://download.companieshouse.gov.uk/en_output.html>. It carries number, name, address,
status, SIC codes and accounts dates, but **not** officers or PSC detail — those are separate
data products on the same site.

Then enrich the ones you care about:

```bash
# clients.csv just needs company numbers in the first column
python companies_house_kyc.py --numbers-file clients.csv --format xlsx
```

`--numbers-file` skips a header row, comments (`#`) and blank lines, and a company number that
fails is logged and skipped rather than losing the whole batch.

### 3. Streaming API

For keeping a loaded snapshot in sync in real time. Needs a separate streaming key — not
covered by this tool.
