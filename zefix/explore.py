#!/usr/bin/env python3
"""Probe the Zefix Public REST API and report what it actually returns.

The point of this script is to answer one question with evidence rather than
assumption: *which* endpoints exist, and *which fields* come back - in
particular whether anything resembling ownership or shareholder data is
exposed.

Usage (credentials via environment):

    export ZEFIX_USER=... ZEFIX_PASSWORD=...
    python zefix/explore.py --spec              # save the OpenAPI document
    python zefix/explore.py --probe             # status code per endpoint
    python zefix/explore.py --company CHE-105.805.185
    python zefix/explore.py --search "Migros" --canton ZH
    python zefix/explore.py --sogc 2026-09-10
    python zefix/explore.py --all --out out/    # everything, saved as JSON

Add ``--test`` to hit the Zefix integration environment instead of production.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from zefix.zefix_client import (  # noqa: E402
    ENDPOINTS,
    PROD_BASE_URL,
    TEST_BASE_URL,
    ZefixClient,
    ZefixError,
)

#: Substrings that would indicate ownership / control data if they ever showed
#: up as field names in a response. Used by ``summarise_fields``.
OWNERSHIP_HINTS = (
    "shareholder",
    "aktionaer",
    "aktionär",
    "gesellschafter",
    "owner",
    "ownership",
    "beneficial",
    "ubo",
    "participation",
    "stammanteil",
    "quota",
    "holder",
    "parent",
    "subsidiary",
    "person",
    "mandate",
    "signature",
)


def collect_field_paths(node: Any, prefix: str = "", out: set[str] | None = None) -> set[str]:
    """Flatten a JSON payload into a set of dotted field paths."""
    if out is None:
        out = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            out.add(path)
            collect_field_paths(value, path, out)
    elif isinstance(node, list):
        for item in node[:5]:  # a sample is enough to learn the shape
            collect_field_paths(item, f"{prefix}[]", out)
    return out


def summarise_fields(label: str, payload: Any) -> None:
    """Print the field inventory of a payload and flag ownership-like keys."""
    paths = sorted(collect_field_paths(payload))
    print(f"\n--- {label}: {len(paths)} distinct field paths ---")
    for path in paths:
        print(f"    {path}")
    hits = [p for p in paths if any(h in p.lower() for h in OWNERSHIP_HINTS)]
    if hits:
        print(f"  !! ownership/person-like fields present: {hits}")
    else:
        print("  -> no ownership/shareholder/person field names in this payload")


def save(out_dir: pathlib.Path | None, name: str, payload: Any) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  saved -> {target}")


def probe(client: ZefixClient, sample_uid: str, sample_date: str) -> None:
    """Call every candidate endpoint and report its HTTP status."""
    print("\n=== Endpoint probe ===")
    placeholders = {
        "{uid}": sample_uid,
        "{chid}": "CH-020.3.926.379-0",
        "{ehraid}": "1",
        "{date}": sample_date,
        "{bfsId}": "261",
    }
    for method, template, description, verified in ENDPOINTS:
        path = template
        for token, value in placeholders.items():
            path = path.replace(token, value)
        note = "documented" if verified else "unverified"
        try:
            payload = client.request(method, path, body={"name": "Migros"} if method == "POST" else None)
            size = len(json.dumps(payload)) if payload is not None else 0
            print(f"  200 {method:4} {template:45} [{note}] {description} ({size} B)")
        except ZefixError as exc:
            print(f"  {exc.status or 'ERR':<3} {method:4} {template:45} [{note}] {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search", metavar="NAME", help="search companies by name")
    parser.add_argument("--canton", help="two-letter canton filter for --search")
    parser.add_argument("--company", metavar="UID", help="fetch full record by UID")
    parser.add_argument("--sogc", metavar="YYYY-MM-DD", help="fetch SOGC publications for a date")
    parser.add_argument("--spec", action="store_true", help="download the OpenAPI document")
    parser.add_argument("--probe", action="store_true", help="status-check every candidate endpoint")
    parser.add_argument("--reference", action="store_true", help="fetch legal forms / communities / registries")
    parser.add_argument("--all", action="store_true", help="run every action with sensible defaults")
    parser.add_argument("--out", metavar="DIR", help="write raw JSON responses to this directory")
    parser.add_argument("--test", action="store_true", help="use the Zefix TEST environment")
    args = parser.parse_args(argv)

    username = os.environ.get("ZEFIX_USER")
    password = os.environ.get("ZEFIX_PASSWORD")
    if not username or not password:
        parser.error(
            "set ZEFIX_USER and ZEFIX_PASSWORD. Credentials are issued by the EHRA "
            "on request - see zefix/README.md."
        )

    client = ZefixClient(
        username=username,
        password=password,
        base_url=TEST_BASE_URL if args.test else PROD_BASE_URL,
    )
    out_dir = pathlib.Path(args.out) if args.out else None
    sample_uid = args.company or "CHE-105.805.185"
    sample_date = args.sogc or "2026-09-10"

    if not any([args.search, args.company, args.sogc, args.spec, args.probe, args.reference, args.all]):
        parser.error("nothing to do: pass at least one action (try --all)")

    if args.spec or args.all:
        print("\n=== OpenAPI document ===")
        try:
            spec = client.openapi_spec()
            paths = sorted((spec or {}).get("paths", {}))
            print(f"  {len(paths)} documented paths:")
            for path in paths:
                methods = ",".join(sorted(spec["paths"][path])).upper()
                print(f"    {methods:12} {path}")
            save(out_dir, "openapi", spec)
        except ZefixError as exc:
            print(f"  could not fetch spec: {exc}")

    if args.probe or args.all:
        probe(client, sample_uid, sample_date)

    if args.search or args.all:
        term = args.search or "Migros"
        print(f"\n=== Search: {term!r} ===")
        results = client.search_companies(term, canton=args.canton)
        print(f"  {len(results)} hits")
        for row in results[:5]:
            print(f"    {row.get('name')} | {row.get('uid')} | {row.get('legalFormId')}")
        if results:
            summarise_fields("search result row", results[0])
        save(out_dir, "search", results)

    if args.company or args.all:
        print(f"\n=== Company: {sample_uid} ===")
        record = client.company_by_uid(sample_uid)
        summarise_fields("company record", record)
        pubs = (record or {}).get("sogcPub") or []
        print(f"  sogcPub entries: {len(pubs)}")
        if pubs:
            summarise_fields("sogcPub entry", pubs[0])
        save(out_dir, "company", record)

    if args.sogc or args.all:
        print(f"\n=== SOGC publications for {sample_date} ===")
        pubs = client.sogc_by_date(sample_date)
        count = len(pubs) if isinstance(pubs, list) else "n/a"
        print(f"  {count} publications")
        if isinstance(pubs, list) and pubs:
            summarise_fields("sogc publication", pubs[0])
        save(out_dir, "sogc", pubs)

    if args.reference or args.all:
        print("\n=== Reference data ===")
        for name, call in (
            ("legalForm", client.legal_forms),
            ("community", client.communities),
            ("registryOffice", client.registry_offices),
        ):
            try:
                payload = call()
                count = len(payload) if isinstance(payload, list) else "n/a"
                print(f"  {name}: {count} entries")
                save(out_dir, name, payload)
            except ZefixError as exc:
                print(f"  {name}: unavailable ({exc})")

    print(
        "\nReminder: a field inventory above with no ownership-like names is the "
        "expected result. See zefix/FINDINGS.md for why."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
