"""Explain WHY an AD/XML pair does not match, one document at a time.

    python3 diagnose.py path/to/SomeCompany_HRA_1234_Flensburg

Point it at a pair folder (containing AD.pdf and SI.xml), or pass the two
files explicitly:

    python3 diagnose.py AD.pdf SI.xml

For every entity the XML has and the AD output does not, it works backwards
through the pipeline and reports the FIRST stage that failed:

  1. Is the entity's text present in the extracted PDF text at all?
     -> No  : the PDF extraction lost it. Nothing the regex can do.
  2. Is that line inside the section block the pattern searches?
     -> No  : the section heading was not recognised, so the pattern never
              saw the line. Fix the section regex, not the entry regex.
  3. Does the entry pattern match the line?
     -> No  : the entry regex is wrong. The report shows how far it got,
              piece by piece, so you can see which part broke.

That ordering matters: the same symptom ("a person is missing") has three
completely different causes, and fixing the wrong one wastes a day.

It also writes the extracted text to <folder>/extracted_text.txt so you can
read exactly what the parser saw.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import hr_a_regex_parser as P

# Optional: only needed to read the PDF. If absent, diagnose.py can still run
# against a pre-extracted .txt file.
try:
    from ad_pipeline import extract_pdf_text
except Exception:
    extract_pdf_text = None

# Optional: the real XML parser. If absent we fall back to comparison.json.
try:
    from kyc_assistant.services.xml_parser import XML_Organizer
except Exception:
    XML_Organizer = None


ENTITY_LISTS = [
    "persoenlich_haftende_gesellschafter",
    "natuerliche_phGs",
    "prokuristen",
    "kommanditisten_personen",
    "kommanditisten_gesellschaften",
]

# Which block each list is extracted from, for the "was it in the block?" check.
LIST_BLOCK = {
    "persoenlich_haftende_gesellschafter": "phg_block",
    "natuerliche_phGs": "phg_block",
    "prokuristen": "prokura_block",
    "kommanditisten_personen": "komm_block",
    "kommanditisten_gesellschaften": "komm_block",
}

BLOCK_SECTION = {
    "phg_block": "PDF 3. b) Inhaber, persoenlich haftende Gesellschafter",
    "prokura_block": "PDF 4. Prokura",
    "komm_block": "PDF 5. c) Kommanditisten, Mitglieder",
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_ad_text(folder: Path, explicit: Path | None) -> tuple[str, str]:
    """Return (raw_text, where_it_came_from)."""
    if explicit is not None:
        candidates = [explicit]
    else:
        candidates = [
            folder / "AD.pdf",
            folder / "extracted_text.txt",
            *sorted(folder.glob("*.pdf")),
            *sorted(folder.glob("*.txt")),
        ]

    for path in candidates:
        if not path.exists():
            continue
        if path.suffix.lower() == ".pdf":
            if extract_pdf_text is None:
                print(f"  ! {path.name} found but no PDF reader available "
                      f"(ad_pipeline/pdfplumber missing) — skipping")
                continue
            return extract_pdf_text(path), path.name
        return path.read_text(encoding="utf-8", errors="replace"), path.name

    raise FileNotFoundError(f"No AD.pdf or .txt found in {folder}")


def _load_xml_data(folder: Path, explicit: Path | None) -> tuple[dict, str]:
    """Return (xml_output_dict, where_it_came_from)."""
    xml_path = explicit
    if xml_path is None:
        for name in ("SI.xml", *[p.name for p in sorted(folder.glob("*.xml"))]):
            if (folder / name).exists():
                xml_path = folder / name
                break

    if xml_path is not None and xml_path.exists() and XML_Organizer is not None:
        organizer = XML_Organizer(xml_content=xml_path.read_text(encoding="utf-8"))
        organizer.create_json_data()
        return organizer.json_data, xml_path.name

    # Fall back to a comparison.json written by an earlier harness run.
    comp = folder / "comparison.json"
    if comp.exists():
        data = json.loads(comp.read_text(encoding="utf-8"))
        rebuilt = {k: v.get("xml_parser") for k, v in data.items() if isinstance(v, dict)}
        return rebuilt, "comparison.json (XML side)"

    raise FileNotFoundError(
        f"No usable XML in {folder}: need SI.xml with XML_Organizer importable, "
        f"or an existing comparison.json"
    )


# ---------------------------------------------------------------------------
# Identifying an entity in the source text
# ---------------------------------------------------------------------------

def _entity_label(rec: dict) -> str:
    adr = rec.get("adresse") or {}
    bet = rec.get("beteiligung") or {}
    bits = [
        rec.get("name") or adr.get("nameKomplett") or "?",
        rec.get("handelsregisternummer") or "",
        rec.get("geburtsdatum") or "",
        adr.get("ort") or "",
    ]
    if bet.get("share"):
        bits.append(f"{bet['share']} {bet.get('waehrung', '')}".strip())
    return " | ".join(str(b) for b in bits if b)


def _search_terms(rec: dict) -> list[str]:
    """Distinctive strings that should appear in the source text for this entity."""
    adr = rec.get("adresse") or {}
    terms = []

    if rec.get("nachname"):
        terms.append(rec["nachname"])
    elif rec.get("name"):
        # company: first couple of words are usually enough and avoid
        # punctuation differences later in the name
        terms.append(" ".join(str(rec["name"]).split()[:2]))

    if rec.get("handelsregisternummer"):
        terms.append(str(rec["handelsregisternummer"]))

    return [t for t in terms if t and len(t) > 2]


def _find_line(text: str, terms: list[str]) -> str | None:
    """The entity's own stretch of text, starting AT its name.

    The fragment must begin at the entity and stop before the next one, or the
    pattern probes below happily match a NEIGHBOURING entry and report that the
    regex is fine when it is not.
    """
    for term in terms:
        idx = text.find(term)
        if idx == -1:
            continue

        # A "Dr. " title, if present, belongs to this entity.
        start = idx
        if text[max(0, idx - 4):idx].strip().endswith("Dr."):
            start = text.rfind("Dr.", max(0, idx - 6), idx)

        # End just after this entity's own amount, if it has one; otherwise cap
        # the window. Either way, stop before the next entry begins.
        tail = text[start:start + 240]
        money = re.search(r"\d[\d.]*,\d{2}\s*[A-ZÄÖÜ]{2,3}\b", tail)
        end = start + (money.end() if money else min(160, len(tail)))
        return text[start:end].strip()
    return None


# ---------------------------------------------------------------------------
# Progressive pattern testing — how far does the regex actually get?
# ---------------------------------------------------------------------------

def _probe_person(fragment: str) -> list[tuple[str, bool, str]]:
    """Run the person patterns piece by piece over a text fragment."""
    kp = re.compile(r"(?:Dr\.\s*)?" + P._PERSON_ANY + r"\s*,?\s*" + P._MONEY, re.S)

    probes = [
        ("surname, given name        (_NAME_HEAD)", P._NAME_HEAD),
        ("+ town and birth date      (_PERSON_WITH_DOB)", P._PERSON_WITH_DOB),
        ("+ town, date optional      (_PERSON_ANY)", P._PERSON_ANY),
        ("+ amount and currency      (_PERSON_ANY + _MONEY)", kp.pattern),
    ]

    results = []
    for label, pattern in probes:
        # re.match, not re.search: the pattern must match THIS entity, starting
        # at its own name. A search would happily match the next entry along
        # and wrongly report the pattern as working.
        m = re.match(pattern, fragment, re.S)
        results.append((label, m is not None, m.group(0)[:70] if m else ""))

    # The amount is the one thing that legitimately appears later in the line.
    money = re.search(P._MONEY, fragment, re.S)
    results.append(("amount and currency        (_MONEY)", money is not None,
                    money.group(0)[:50] if money else ""))
    return results


def _probe_org(fragment: str) -> list[tuple[str, bool, str]]:
    m = P._ORG_WITH_REGISTER.match(fragment)
    money = re.search(P._MONEY, fragment, re.S)
    return [
        ("name, town (court, HR-no)  (_ORG_WITH_REGISTER)", m is not None,
         m.group(0)[:70] if m else ""),
        ("amount and currency        (_MONEY)", money is not None,
         money.group(0)[:50] if money else ""),
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def diagnose(folder: Path, ad_file: Path | None = None, xml_file: Path | None = None) -> int:
    print("=" * 78)
    print(f"DIAGNOSING  {folder.name}")
    print("=" * 78)

    raw_text, ad_src = _load_ad_text(folder, ad_file)
    xml_data, xml_src = _load_xml_data(folder, xml_file)
    ad_data = P.parse_handelsregister_a_text(raw_text)

    blocks = P.section_blocks(raw_text)
    t = blocks["text"]

    out_txt = folder / "extracted_text.txt"
    try:
        out_txt.write_text(raw_text, encoding="utf-8")
        wrote = f"  (full extracted text written to {out_txt.name})"
    except Exception:
        wrote = ""

    print(f"\nAD source  : {ad_src}")
    print(f"XML source : {xml_src}")
    print(f"text length: {len(t)} chars{wrote}")

    # ---- section blocks ---------------------------------------------------
    print("\nSECTION BLOCKS")
    for key in ("phg_block", "prokura_block", "komm_block"):
        body = blocks[key]
        status = f"{len(body):>6} chars" if body else "  EMPTY  <-- heading not recognised"
        print(f"  {key:<15} {status}   [{BLOCK_SECTION[key]}]")

    # ---- per-list comparison ---------------------------------------------
    problems = 0

    for list_name in ENTITY_LISTS:
        xml_list = xml_data.get(list_name) or []
        ad_list = ad_data.get(list_name) or []

        if not isinstance(xml_list, list):
            continue

        def key(rec):
            adr = (rec or {}).get("adresse") or {}
            return (
                str((rec or {}).get("name", "")),
                str(adr.get("nameKomplett", "")),
                str((rec or {}).get("handelsregisternummer", "")),
            )

        ad_keys = {key(r) for r in ad_list if isinstance(r, dict)}
        missing = [r for r in xml_list if isinstance(r, dict) and key(r) not in ad_keys]
        extra = [r for r in ad_list if isinstance(r, dict) and key(r) not in {key(x) for x in xml_list if isinstance(x, dict)}]

        if not missing and not extra:
            print(f"\n{list_name}: OK  ({len(xml_list)} in XML, {len(ad_list)} in AD)")
            continue

        problems += len(missing)
        print(f"\n{list_name}: {len(xml_list)} in XML, {len(ad_list)} in AD")

        for rec in missing:
            print(f"\n  MISSING FROM AD: {_entity_label(rec)}")

            terms = _search_terms(rec)
            fragment = _find_line(t, terms)

            # --- stage 1: is it in the extracted text at all? --------------
            if fragment is None:
                print(f"    stage 1  text present in PDF extract? NO")
                print(f"             searched for: {terms}")
                print(f"    => ROOT CAUSE: the PDF extraction never produced this text.")
                print(f"       The regex cannot recover it. Check the PDF reader / table")
                print(f"       handling, and read {out_txt.name} to confirm.")
                continue

            print(f"    stage 1  text present in PDF extract? yes")
            print(f"             ...{fragment}...")

            # --- stage 2: is it inside the right block? -------------------
            block_key = LIST_BLOCK[list_name]
            block_body = blocks[block_key]
            anchor = terms[0] if terms else ""
            in_block = bool(block_body) and anchor in block_body

            print(f"    stage 2  inside {block_key}? {'yes' if in_block else 'NO'}")
            if not in_block:
                heading_hint = ""
                idx = t.find(anchor)
                if idx != -1:
                    before = t[max(0, idx - 220):idx]
                    hits = re.findall(
                        r"(?:\d+\s*\.\s*)?[a-z]?\)?\s*"
                        r"(?:Kommanditist\w*|Prokura|Inhaber|Persönlich haftende\w*)[^,:]{0,30}",
                        before,
                    )
                    if hits:
                        heading_hint = hits[-1].strip()
                print(f"    => ROOT CAUSE: the section block does not contain this line,")
                print(f"       so the entry pattern never saw it. Fix the SECTION regex")
                print(f"       ({block_key}), not the entry regex.")
                if heading_hint:
                    print(f"       Heading printed just above it: {heading_hint!r}")
                    print(f"       Compare that against the section pattern in the parser.")
                continue

            # --- stage 3: does the entry pattern match? -------------------
            is_company = bool(rec.get("name")) and not rec.get("nachname")
            probes = _probe_org(fragment) if is_company else _probe_person(fragment)

            print(f"    stage 3  entry pattern, piece by piece:")
            first_fail = None
            for label, ok, sample in probes:
                mark = "ok  " if ok else "FAIL"
                print(f"               [{mark}] {label}")
                if ok and sample:
                    print(f"                      matched: {sample!r}")
                if not ok and first_fail is None:
                    first_fail = label

            if first_fail:
                print(f"    => ROOT CAUSE: the entry pattern breaks at: {first_fail}")
                print(f"       That is the piece to fix in hr_a_regex_parser.py.")
            else:
                print(f"    => Pattern matches this fragment in isolation, but the entry")
                print(f"       still did not reach the output. Likely the de-duplication")
                print(f"       check treated it as a repeat of another entry.")

        for rec in extra:
            print(f"\n  ONLY IN AD (not in XML): {_entity_label(rec)}")
            print(f"    => the AD side invented or mis-attributed this entry.")

    print("\n" + "=" * 78)
    if problems:
        print(f"{problems} entit{'y' if problems == 1 else 'ies'} missing from the AD output — see ROOT CAUSE lines above.")
    else:
        print("No entities missing. Any remaining mismatch is in field VALUES,")
        print("not in which entities were found — compare comparison.json directly.")
    print("=" * 78)
    return problems


def main() -> None:
    args = [Path(a) for a in sys.argv[1:]]

    if not args:
        print(__doc__)
        sys.exit(1)

    if len(args) == 1 and args[0].is_dir():
        sys.exit(1 if diagnose(args[0]) else 0)

    pdfs = [a for a in args if a.suffix.lower() in (".pdf", ".txt", ".md")]
    xmls = [a for a in args if a.suffix.lower() == ".xml"]
    folder = (pdfs or xmls)[0].parent

    sys.exit(1 if diagnose(
        folder,
        ad_file=pdfs[0] if pdfs else None,
        xml_file=xmls[0] if xmls else None,
    ) else 0)


if __name__ == "__main__":
    main()
