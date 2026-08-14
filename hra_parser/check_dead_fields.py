"""Answers one question: does the XML side ever populate the 7 schema fields
that hr_a_regex_parser.py currently never writes to?

    leitende_personen, board, company_owner, vertreter,
    persoenlich_haftende_gesellschafter_ohne_vertretung,
    natuerliche_phGs_ohne_vertretung, unternehmen.geschaeftsfuehrer

It reads every comparison.json your existing test harness already writes (one
per document pair, under assets/ad_si_pairs/<doc>/comparison.json) and reports,
per field: how many documents had a non-empty XML value, and a few real
examples so we know the shape of the data if it turns out to matter.

Run it from wherever your ad_si_pairs folder lives:

    python3 check_dead_fields.py /path/to/assets/ad_si_pairs

If you omit the path, it looks for ./assets/ad_si_pairs relative to wherever
you run it from.
"""

import json
import sys
from collections import Counter
from pathlib import Path

DEAD_LIST_FIELDS = [
    "leitende_personen",
    "board",
    "company_owner",
    "vertreter",
    "persoenlich_haftende_gesellschafter_ohne_vertretung",
    "natuerliche_phGs_ohne_vertretung",
]

# unternehmen.geschaeftsfuehrer is a string, not a list, and lives one level
# deeper — handled separately below.


def _is_nonempty(value) -> bool:
    if value in (None, "", "<missing>"):
        return False
    if isinstance(value, list):
        return len(value) > 0
    return True


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/ad_si_pairs")

    if not root.exists():
        print(f"Path not found: {root}")
        print("Pass the folder that holds one subfolder per document, each")
        print("containing a comparison.json — e.g.:")
        print("  python3 check_dead_fields.py C:\\...\\assets\\ad_si_pairs")
        sys.exit(1)

    comparison_files = sorted(root.glob("*/comparison.json"))
    if not comparison_files:
        print(f"No comparison.json files found under {root}")
        print("Run your comparison harness (phase_compare) first.")
        sys.exit(1)

    print(f"Scanning {len(comparison_files)} comparison.json files under {root}\n")

    hit_counts: Counter = Counter()
    examples: dict[str, list] = {f: [] for f in DEAD_LIST_FIELDS}
    geschaeftsfuehrer_hits = 0
    geschaeftsfuehrer_examples: list = []
    unreadable = 0

    for path in comparison_files:
        doc_name = path.parent.name

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            unreadable += 1
            print(f"  [skip] could not read {path}: {exc}")
            continue

        for field in DEAD_LIST_FIELDS:
            entry = data.get(field, {})
            xml_val = entry.get("xml_parser") if isinstance(entry, dict) else None
            if _is_nonempty(xml_val):
                hit_counts[field] += 1
                if len(examples[field]) < 3:
                    examples[field].append((doc_name, xml_val))

        u_entry = data.get("unternehmen", {})
        xml_u = u_entry.get("xml_parser") if isinstance(u_entry, dict) else None
        if isinstance(xml_u, dict) and _is_nonempty(xml_u.get("geschaeftsfuehrer")):
            geschaeftsfuehrer_hits += 1
            if len(geschaeftsfuehrer_examples) < 3:
                geschaeftsfuehrer_examples.append((doc_name, xml_u.get("geschaeftsfuehrer")))

    total = len(comparison_files) - unreadable

    print("=" * 78)
    print(f"{'field':<55} {'docs with XML data':>20}")
    print("-" * 78)
    for field in DEAD_LIST_FIELDS:
        count = hit_counts[field]
        marker = "  <-- XML HAS THIS, code doesn't" if count else ""
        print(f"{field:<55} {count:>6} / {total:<10}{marker}")
    marker = "  <-- XML HAS THIS, code doesn't" if geschaeftsfuehrer_hits else ""
    print(f"{'unternehmen.geschaeftsfuehrer':<55} {geschaeftsfuehrer_hits:>6} / {total:<10}{marker}")
    print("=" * 78)

    any_hits = any(hit_counts.values()) or geschaeftsfuehrer_hits

    if not any_hits:
        print("\nNone of the 7 fields ever have XML data in this corpus.")
        print("These fields are genuinely not needed for this document type —")
        print("nothing to build.")
    else:
        print("\nExamples (doc name, XML value) for fields that DO have data:")
        for field in DEAD_LIST_FIELDS:
            if examples[field]:
                print(f"\n  {field}:")
                for doc_name, val in examples[field]:
                    print(f"    {doc_name}: {json.dumps(val, ensure_ascii=False)[:300]}")
        if geschaeftsfuehrer_examples:
            print("\n  unternehmen.geschaeftsfuehrer:")
            for doc_name, val in geschaeftsfuehrer_examples:
                print(f"    {doc_name}: {json.dumps(val, ensure_ascii=False)[:300]}")
        print("\nSend me this output (or just the summary table + a couple of the")
        print("examples above) and I'll build extraction for exactly the fields")
        print("that showed up, using the real shape of the data.")

    if unreadable:
        print(f"\n({unreadable} file(s) could not be read — see [skip] lines above)")


if __name__ == "__main__":
    main()
