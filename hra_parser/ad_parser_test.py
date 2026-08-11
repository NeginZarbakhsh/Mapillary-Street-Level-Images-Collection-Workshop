from __future__ import annotations

import json
import sys
import traceback
from collections import Counter
from pathlib import Path

from tqdm import tqdm

from kyc_assistant.services.xml_parser import XML_Organizer
from kyc_assistant.services.pdf_processor import parse_ad_pdf_to_schema as parse_ad

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
AD_DOCS_DIR = Path(__file__).parent / "assets" / "ad_docs"
PAIRS_DIR = Path(__file__).parent / "assets" / "ad_si_pairs"

# Lists whose order carries no meaning — sorted before comparing.
UNORDERED_LISTS = {
    "kommanditisten_personen",
    "persoenlich_haftende_gesellschafter",
    "kommanditisten_gesellschaften",
    "prokuristen",
    "natuerliche_phGs",
    "natuerliche_phGs_ohne_vertretung",
    "persoenlich_haftende_gesellschafter_ohne_vertretung",
    "leitende_personen",
    "board",
    "company_owner",
    "vertreter",
}

# Fields excluded from the match statistic entirely (not silently counted as OK).
IGNORED_FIELDS = {"to_highlight"}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
def _run_xml_parser(si_path: Path) -> dict | None:
    """Run XML_Organizer on an SI file and return its json_data dict."""
    try:
        content = si_path.read_text(encoding="utf-8")
        organizer = XML_Organizer(xml_content=content)
        organizer.create_json_data()
        return organizer.json_data
    except Exception as exc:
        print(f"  [ERROR] XML_Organizer failed on {si_path.name}: {exc}")
        return None


def _strip_bundesland(items: list) -> list:
    cleaned = []
    for it in items:
        c = json.loads(json.dumps(it))  # deep copy
        if isinstance(c, dict):
            c.get("adresse", {}).pop("bundesland", None)
        cleaned.append(c)
    return cleaned


def _normalize_obj(obj):
    if isinstance(obj, str):
        return obj.strip()

    if isinstance(obj, list):
        return [_normalize_obj(x) for x in obj]

    if isinstance(obj, dict):
        return {k: _normalize_obj(v) for k, v in obj.items()}

    return obj


def _sort_entity_list(items: list) -> list:
    """Sort parser output lists so XML and AD can match even if order differs."""
    def key(x):
        if not isinstance(x, dict):
            return ("", "", "", "")
        return (
            str(x.get("name", "")),
            str(x.get("handelsregisternummer", "")),
            str((x.get("adresse") or {}).get("nameKomplett", "")),
            str(x.get("geburtsdatum", "")),
        )

    return sorted(items, key=key)


def _prepare(key: str, value):
    """Apply the same cleaning used for the match decision, for diffing."""
    if key in UNORDERED_LISTS and isinstance(value, list):
        return _sort_entity_list(_normalize_obj(_strip_bundesland(value)))
    return _normalize_obj(value)


# ---------------------------------------------------------------------------
# Mismatch description — this is what makes a failure readable
# ---------------------------------------------------------------------------
def _entity_label(item) -> str:
    """One-line identity for a person/company record."""
    if not isinstance(item, dict):
        return repr(item)

    adr = item.get("adresse") or {}
    bet = item.get("beteiligung") or {}

    parts = [
        item.get("name") or adr.get("nameKomplett") or "?",
        item.get("handelsregisternummer") or "",
        item.get("geburtsdatum") or "",
        adr.get("ort") or "",
        adr.get("land") or "",
    ]
    share = bet.get("share")
    if share:
        parts.append(f"{share} {bet.get('waehrung', '')}".strip())

    return " | ".join(str(p) for p in parts if p)


def _loose_key(item):
    """Identity used to pair up an XML record with its AD counterpart."""
    if not isinstance(item, dict):
        return repr(item)

    adr = item.get("adresse") or {}
    return (
        str(item.get("name", "")),
        str(adr.get("nameKomplett", "")),
        str(item.get("handelsregisternummer", "")),
    )


def _flat_diff(xml_val, ad_val, prefix="") -> list[tuple[str, object, object]]:
    """Recursively collect leaf-level differences as (path, xml, ad)."""
    if isinstance(xml_val, dict) and isinstance(ad_val, dict):
        diffs = []
        for k in sorted(set(xml_val) | set(ad_val)):
            diffs += _flat_diff(
                xml_val.get(k, "<missing>"),
                ad_val.get(k, "<missing>"),
                f"{prefix}.{k}",
            )
        return diffs

    if xml_val != ad_val:
        return [(prefix or ".", xml_val, ad_val)]

    return []


def _describe_mismatch(key: str, xml_val, ad_val) -> list[str]:
    """Human-readable lines explaining why a field failed."""
    lines: list[str] = []

    if isinstance(xml_val, list) and isinstance(ad_val, list):
        xml_by_key = {_loose_key(x): x for x in xml_val}
        ad_by_key = {_loose_key(a): a for a in ad_val}

        missing = [xml_by_key[k] for k in xml_by_key if k not in ad_by_key]
        extra = [ad_by_key[k] for k in ad_by_key if k not in xml_by_key]
        common = [k for k in xml_by_key if k in ad_by_key]

        if missing:
            lines.append(f"    missing from AD ({len(missing)}):")
            lines += [f"      - {_entity_label(m)}" for m in missing]

        if extra:
            lines.append(f"    only in AD ({len(extra)}):")
            lines += [f"      + {_entity_label(e)}" for e in extra]

        for k in common:
            for path, xv, av in _flat_diff(xml_by_key[k], ad_by_key[k]):
                lines.append(
                    f"    {_entity_label(xml_by_key[k])}\n"
                    f"      {path}: XML={xv!r}  AD={av!r}"
                )
        return lines

    for path, xv, av in _flat_diff(xml_val, ad_val):
        lines.append(f"    {path}: XML={xv!r}  AD={av!r}")

    return lines or [f"    XML={xml_val!r}  AD={ad_val!r}"]


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def _compare_dicts(xml_data: dict, ad_data: dict) -> dict:
    """Produce a field-level comparison dict between XML and AD parser outputs."""
    all_keys = sorted(set(xml_data) | set(ad_data))
    comparison: dict = {}

    for key in all_keys:
        xml_val = xml_data.get(key, "<missing>")
        ad_val = ad_data.get(key, "<missing>")

        if key in IGNORED_FIELDS:
            comparison[key] = {
                "xml_parser": xml_val,
                "ad_parser": ad_val,
                "match": None,          # None = not counted, not "passed"
                "skipped": True,
            }
            continue

        match = _prepare(key, xml_val) == _prepare(key, ad_val)

        entry = {
            "xml_parser": xml_val,
            "ad_parser": ad_val,
            "match": match,
        }
        if not match:
            entry["why"] = _describe_mismatch(key, _prepare(key, xml_val), _prepare(key, ad_val))

        comparison[key] = entry

    return comparison


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------
def phase_compare(pairs_dir: Path = PAIRS_DIR, only: str | None = None) -> int:
    """Run both parsers over every pair, write comparison JSONs, report mismatches.

    Returns the number of pairs that had at least one mismatched field.
    """
    print("\n" + "=" * 80)
    print("PHASE 2 — Compare XML-parser vs AD-parser")
    print("=" * 80)

    sub_dirs = sorted(d for d in pairs_dir.iterdir() if d.is_dir())
    if not sub_dirs:
        print(f"No sub-directories found in {pairs_dir}.  Run phase_download() first.")
        return 0

    total_fields = 0
    matched_fields = 0
    missing_si = 0
    compared = 0
    parser_errors: list[str] = []

    field_failures: Counter = Counter()
    pair_reports: list[str] = []
    failing_pairs: list[str] = []

    for sub in tqdm(sub_dirs):
        if "HRA" not in sub.name:
            continue
        if only and only.lower() not in sub.name.lower():
            continue

        ad_file = sub / "AD.pdf"
        si_file = sub / "SI.xml"

        if not ad_file.exists():
            continue  # folder not yet complete

        # A crash on one document must not kill a 245-document run.
        try:
            ad_data = parse_ad(ad_file)
        except Exception:
            parser_errors.append(sub.name)
            print(f"  [ERROR] AD parser raised on {sub.name}:")
            traceback.print_exc()
            ad_data = {}

        if ad_data is None:
            print(f"  [WARN] AD parser returned None for {ad_file}")
            ad_data = {}

        xml_data: dict = {}
        if si_file.exists():
            result = _run_xml_parser(si_file)
            if result is not None:
                xml_data = result
        else:
            missing_si += 1
            print(f"  [WARN] No SI file in {sub.name}")
            continue  # nothing to compare against

        compared += 1
        comparison = _compare_dicts(xml_data, ad_data)

        for v in comparison.values():
            if v["match"] is None:      # skipped field
                continue
            total_fields += 1
            if v["match"]:
                matched_fields += 1

        (sub / "comparison.json").write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        bad = {k: v for k, v in comparison.items() if v["match"] is False}
        if bad:
            failing_pairs.append(sub.name)
            block = [f"### {sub.name}"]
            for key, v in bad.items():
                field_failures[key] += 1
                block.append(f"  {key}")
                block += v.get("why", [])
            pair_reports.append("\n".join(block))

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Pairs compared        : {compared}")
    print(f"Pairs fully matching  : {compared - len(failing_pairs)}")
    print(f"Pairs with mismatches : {len(failing_pairs)}")
    if missing_si:
        print(f"Missing SI            : {missing_si}")
    if parser_errors:
        print(f"AD parser crashes     : {len(parser_errors)}  -> {', '.join(parser_errors[:5])}")
    if total_fields:
        pct = matched_fields / total_fields * 100
        print(f"Field match           : {matched_fields}/{total_fields}  ({pct:.1f}%)")
        print(f"  (excluding {', '.join(sorted(IGNORED_FIELDS))})")

    if field_failures:
        print("\nWorst fields:")
        width = max(len(k) for k in field_failures)
        for key, count in field_failures.most_common():
            share = count / compared * 100 if compared else 0
            print(f"  {key.ljust(width)}  {count:>4} pairs  ({share:4.1f}%)")

    if pair_reports:
        report_path = pairs_dir / "mismatch_report.txt"
        report_path.write_text("\n\n".join(pair_reports), encoding="utf-8")
        print(f"\nPer-pair detail written to: {report_path}")
        print("\nFirst failing pair:\n")
        print(pair_reports[0])

    return len(failing_pairs)


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    failed = phase_compare(PAIRS_DIR, only=only)
    sys.exit(1 if failed else 0)
