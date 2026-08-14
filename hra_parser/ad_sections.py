"""Normalise a Handelsregister-A printout, split it into its numbered sections,
and audit which lines the regex parser failed to consume.

Intended use:

    from ad_sections import normalize_ad_text, split_sections, audit_extraction
    from hr_a_regex_parser import parse_handelsregister_a_text

    clean = normalize_ad_text(raw_pdf_text)      # tables flattened, furniture gone
    parsed = parse_handelsregister_a_text(clean)
    report = audit_extraction(clean, parsed)     # what did the parser walk past?

The audit is deliberately parser-agnostic: it asks whether every line carrying a
birth date, a money amount or a register number is represented somewhere in the
parser output. Anything that is not gets reported with the section it came from,
so a missed entry points straight at the section whose pattern needs work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 1) Table flattening
# ---------------------------------------------------------------------------

# A markdown alignment row: |---|:--:|----|
_MD_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$")

# Box-drawing and rule characters a PDF-to-text converter may emit.
_BOX_CHARS = str.maketrans({c: " " for c in "│┃┆┇┊┋├┤┬┴┼─━┄┅┈┉┌┐└┘╔╗╚╝║═╠╣╦╩╬"})


def flatten_tables(text: str) -> str:
    """Turn table rows into ordinary comma-separated prose.

    A Kommanditist printed as

        | Hellmich, Walter Georg | Luxemburg / Luxemburg | *24.03.1944 | 1.000.000,00 EUR |

    becomes

        Hellmich, Walter Georg, Luxemburg / Luxemburg, *24.03.1944, 1.000.000,00 EUR

    which is the same shape the register prints inline, so one set of patterns
    covers both. Cell order is preserved; only the delimiters change.
    """
    lines: list[str] = []

    for raw in text.translate(_BOX_CHARS).split("\n"):
        line = raw.rstrip()

        if _MD_SEPARATOR.match(line) and "-" in line:
            continue  # alignment row carries no data

        if line.count("|") >= 2 or line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                lines.append(", ".join(cells))
            continue

        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2) Normalisation
# ---------------------------------------------------------------------------

# Running headers/footers repeated on every printed page.
_PAGE_FURNITURE = re.compile(
    r"(?im)^[ \t]*(?:"
    r"Seite\s+\d+\s+von\s+\d+"
    r"|-{0,3}\s*Wiedergabe des aktuellen Registerinhalts\s*-{0,3}"
    r"|-{0,3}\s*Handelsregister\s+Abteilung\s+[AB]\s*-{0,3}"
    r"|---\s*page\s+\d+\s*---"
    r"|\f"
    r")[ \t]*$"
)

_MD_EMPHASIS = re.compile(r"\*\*|__|`{1,3}")


def normalize_ad_text(text: str) -> str:
    """Flatten tables, drop page furniture and repair hyphenated line breaks.

    Line structure is preserved — the section splitter and the audit both need
    it. Collapsing to a single line, as the parser does internally, is fine
    afterwards.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    text = flatten_tables(text)

    # Markdown emphasis markers, but not the "*" that prefixes a birth date.
    text = _MD_EMPHASIS.sub(" ", text)

    text = _PAGE_FURNITURE.sub("", text)

    # "Flens-\nburg" -> "Flensburg"; "Risum-\nLindholm" keeps its real hyphen.
    text = re.sub(r"(\w)-[ \t]*\n[ \t]*(?=[a-zäöüß])", r"\1", text)
    text = re.sub(r"(\w-)[ \t]*\n[ \t]*(?=[A-ZÄÖÜ])", r"\1", text)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# 3) Section splitting
# ---------------------------------------------------------------------------

# A heading opens with "5." or "a)" or "5. a)". Continuation headings ("b) Sitz")
# inherit the number of the last numbered heading.
_HEADING = re.compile(
    r"^[ \t]*(?:"
    r"(?P<num>\d{1,2})\s*\.\s*(?:(?P<letter_a>[a-z])\s*\))?"
    r"|(?P<letter_b>[a-z])\s*\)"
    r")\s*(?P<title>[A-Za-zÄÖÜäöüß].*)$"
)

# What each section is expected to yield, used to flag a section that produced
# nothing at all.
# Which output lists a data-bearing section is expected to fill. A section that
# carries signals but leaves every one of its targets empty is reported.
SECTION_ROLE = {
    "3b": ("persoenlich_haftende_gesellschafter", "natuerliche_phGs"),
    "4": ("prokuristen",),
    "5c": ("kommanditisten_personen", "kommanditisten_gesellschaften"),
}


@dataclass
class Section:
    key: str                  # "5c"
    title: str                # "Kommanditisten, Mitglieder"
    lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.lines)

    @property
    def role(self) -> tuple:
        return SECTION_ROLE.get(self.key, ())


def split_sections(text: str) -> list[Section]:
    """Split normalised text into its numbered/lettered register sections."""
    sections: list[Section] = []
    current = Section(key="0", title="<header>")
    last_num = ""

    for line in text.split("\n"):
        if not line.strip():
            continue

        m = _HEADING.match(line)
        # A heading is only a heading if a title follows; "1." alone is a list index.
        if m and m.group("title").strip():
            num = m.group("num") or last_num
            letter = m.group("letter_a") or m.group("letter_b") or ""

            if m.group("num"):
                last_num = m.group("num")

            sections.append(current)
            current = Section(key=f"{num}{letter}", title=m.group("title").strip())
            continue

        current.lines.append(line)

    sections.append(current)
    return [s for s in sections if s.lines or s.title != "<header>"]


# ---------------------------------------------------------------------------
# 4) Extraction audit
# ---------------------------------------------------------------------------

# A line worth extracting carries at least one of: a birth date, a money amount,
# or a register reference.
_SIGNALS = re.compile(
    r"\*\s*\d{2}\.\d{2}\.\d{4}"
    r"|\d[\d.]*,\d{2}\s*[A-ZÄÖÜ]{2,3}\b"
    r"|(?:HRA|HRB|GnR|PR|VR)\s*\d+"
)

_ENTITY_LISTS = (
    "persoenlich_haftende_gesellschafter",
    "kommanditisten_personen",
    "kommanditisten_gesellschaften",
    "natuerliche_phGs",
    "natuerliche_phGs_ohne_vertretung",
    "persoenlich_haftende_gesellschafter_ohne_vertretung",
    "prokuristen",
    "leitende_personen",
    "board",
    "company_owner",
    "vertreter",
)


def _iso_to_german(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else ""


def _en_money_to_german(value) -> str:
    try:
        whole, frac = f"{float(value):.2f}".split(".")
    except (TypeError, ValueError):
        return ""
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{grouped},{frac}"


def _claimed_tokens(parsed: dict) -> set[str]:
    """Distinctive strings that appear in the source when a record was extracted."""
    tokens: set[str] = set()

    def add(value):
        if isinstance(value, str) and len(value.strip()) > 2:
            tokens.add(value.strip())

    company = parsed.get("unternehmen") or {}
    add(company.get("name"))
    add(company.get("handelsregisternummer"))
    for part in ("strasse", "plz", "ort"):
        add((company.get("adresse") or {}).get(part))

    for key in _ENTITY_LISTS:
        for rec in parsed.get(key) or []:
            if not isinstance(rec, dict):
                continue
            add(rec.get("name"))
            add(rec.get("handelsregisternummer"))
            add(rec.get("nachname"))
            add((rec.get("adresse") or {}).get("nameKomplett"))
            add(_iso_to_german(rec.get("geburtsdatum") or ""))
            share = (rec.get("beteiligung") or {}).get("share")
            if share:
                add(_en_money_to_german(share))

    return tokens


def audit_extraction(text: str, parsed: dict) -> dict:
    """Report source lines that carry data but are absent from the parser output.

    Returns {"missed": [...], "empty_sections": [...], "sections": [...]}.
    Each missed entry is {"section", "title", "line"}.
    """
    sections = split_sections(text)
    tokens = _claimed_tokens(parsed)

    missed: list[dict] = []
    empty_sections: list[dict] = []

    for section in sections:
        signal_lines = [ln for ln in section.lines if _SIGNALS.search(ln)]

        for line in signal_lines:
            if not any(tok in line for tok in tokens):
                missed.append(
                    {"section": section.key, "title": section.title, "line": line.strip()}
                )

        if section.role and signal_lines:
            # A data-bearing section that filled none of its target lists.
            if not any(parsed.get(key) for key in section.role):
                empty_sections.append({
                    "section": section.key,
                    "title": section.title,
                    "expected": " / ".join(section.role),
                })

    return {
        "missed": missed,
        "empty_sections": empty_sections,
        "sections": [{"key": s.key, "title": s.title, "lines": len(s.lines)} for s in sections],
    }


def format_audit(report: dict) -> str:
    """Render an audit as readable text."""
    out: list[str] = []

    if report["missed"]:
        out.append(f"UNEXTRACTED LINES ({len(report['missed'])})")
        for item in report["missed"]:
            out.append(f"  [{item['section']}] {item['title']}")
            out.append(f"      {item['line']}")
    else:
        out.append("UNEXTRACTED LINES (0) — every data line is represented in the output")

    if report["empty_sections"]:
        out.append("")
        out.append(f"SECTIONS WITH DATA BUT NO OUTPUT ({len(report['empty_sections'])})")
        for item in report["empty_sections"]:
            out.append(f"  [{item['section']}] {item['title']}  -> {item['expected']} is empty")

    out.append("")
    out.append("SECTIONS FOUND")
    for s in report["sections"]:
        out.append(f"  {s['key']:>4}  {s['title'][:60]:<60} {s['lines']:>3} lines")

    return "\n".join(out)
