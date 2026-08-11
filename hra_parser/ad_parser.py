"""Parser for German Handelsregister Abteilung A printouts ("Aktueller Ausdruck").

The output is deliberately shaped to be diff-identical to the XJustiz parser in
``kyc_assistant.services.xml_parser`` so that an AD printout and the matching XML
message can be compared field by field.
"""

import html
import logging
import re
from copy import deepcopy
from datetime import datetime

from kyc_assistant.domain.legal_forms import extract_rechtsform, rechtsform_map

# Shared Bundesland dataset (same source as xml_parser) — adjust path if needed
try:
    from kyc_assistant.services.xml_parser import get_bundesland_data
except Exception:
    get_bundesland_data = None

log = logging.getLogger(__name__)

# The XJustiz parser leaves ``eintragungsdatum`` / ``letzte_aenderung`` empty even
# though the XML carries <gruendungsdatum> and <letzteEintragung>. The AD printout
# does contain both dates, so we only fill them in once the XML side populates them
# too — otherwise every comparison would report a false mismatch.
EMIT_REGISTRATION_DATES = False

BASE_OUTPUT = {
    "unternehmen": {
        "name": "",
        "rechtsform": "",
        "handelsregisternummer": "",
        "registergericht": "",
        "amtsgericht_verbatim": "",
        "geschaeftsfuehrer": "",
        "adresse": {
            "nameKomplett": "",
            "strasse": "",
            "hausnummer": "",
            "plz": "",
            "ort": "",
            "bundesland": "",
            "land": "DE",
        },
        "eintragungsdatum": "",
        "letzte_aenderung": "",
    },
    "persoenlich_haftende_gesellschafter": [],
    "kommanditisten_personen": [],
    "kommanditisten_gesellschaften": [],
    "to_highlight": [],
    "persoenlich_haftende_gesellschafter_ohne_vertretung": [],
    "leitende_personen": [],
    "prokuristen": [],
    "board": [],
    "company_owner": [],
    "vertreter": [],
    "natuerliche_phGs": [],
    "natuerliche_phGs_ohne_vertretung": [],
}

CITY_TO_BUNDESLAND = {
    "Ahlen": "Nordrhein-Westfalen",
    "Münster": "Nordrhein-Westfalen",
    "Muenster": "Nordrhein-Westfalen",
    "Flensburg": "Schleswig-Holstein",
    "Nordhackstedt": "Schleswig-Holstein",
    "Handewitt": "Schleswig-Holstein",
}


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def _norm(value: str) -> str:
    """
    Normalise text:
    - convert HTML entities like &amp; to &
    - collapse spaces/tabs
    - strip leading/trailing whitespace
    """
    value = html.unescape(value or "")
    return re.sub(r"[ \t]+", " ", value).strip()


def _to_iso_date(ddmmyyyy: str) -> str:
    """
    Convert German date format DD.MM.YYYY to ISO YYYY-MM-DD.
    """
    try:
        return datetime.strptime(ddmmyyyy, "%d.%m.%Y").strftime("%Y-%m-%d")
    except Exception:
        return ""


def _german_money_to_en(value: str) -> str:
    """
    Convert German money format:
    5.940.225,00 -> 5940225.00
    """
    value = (value or "").replace(".", "").replace(",", ".")

    try:
        return f"{float(value):.2f}"
    except Exception:
        return "0.00"


def _legal_form_number(name_or_form_text: str) -> str:
    """
    Map legal form text/name to project-specific legal form code.
    """
    rf_key = extract_rechtsform(name_or_form_text or "")
    return str(rechtsform_map.get(rf_key, "") or "")


def _append_hl(out: dict, value: str) -> None:
    """
    Append a value to to_highlight.

    Duplicates are kept on purpose: the XML parser emits the same town twice when
    the company and its general partner share a seat, so de-duplicating here would
    desynchronise the two lists.
    """
    value = _norm(value)

    if value:
        out["to_highlight"].append(value)


def _get_bundesland(city: str, plz: str = "") -> str:
    """
     SAME dataset the XML parser uses,
    so AD and XML outputs agree. Prefer PLZ, then city, then manual fallback.
    """
    city = _norm(city)
    plz = _norm(plz)

    if get_bundesland_data is not None:
        try:
            data = get_bundesland_data()
            if plz and plz in data.plz_bundesland_mapping:
                return data.plz_bundesland_mapping[plz].bundesland
            if city:
                bl = data.get_bundesland_by_ort(city)
                if bl:
                    return bl
        except Exception:
            pass

    return CITY_TO_BUNDESLAND.get(city, "")


# ----------------------------------------------------------------------------
# text preparation
# ----------------------------------------------------------------------------

# Running headers/footers that a two-column AD printout repeats on every page.
# Stripped only from the text used for section parsing — the company-level fields
# are read from the full text before this runs.
_PAGE_NOISE_RE = re.compile(
    r"(?m)^[ \t]*(?:"
    r"Seite\s+\d+\s+von\s+\d+"
    r"|Ausdruck"
    r"|Aktueller Ausdruck"
    r"|-\s*Wiedergabe des aktuellen Registerinhalts\s*-"
    r"|-\s*Handelsregister\s+Abteilung\s+[AB]\s*-"
    r"|Handelsregister\s+Abteilung\s+[AB]"
    r"|Abruf vom\s+\d{2}\.\d{2}\.\d{4}(?:\s*,\s*\d{1,2}[:.]\d{2})?"
    r"|(?:HRA|HRB)\s*\d+(?:\s+[A-ZÄÖÜ]{1,3})?"
    r"|Amtsgericht\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]*"
    r"|\d{2}\.\d{2}\.\d{4}"
    r")[ \t]*$"
)


def _prepare(text: str) -> str:
    """Unescape entities and normalise line endings / stray form feeds."""
    text = html.unescape(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    return text


def _dehyphenate(text: str) -> str:
    """
    Repair words broken across a line break by the PDF renderer.

    ``Flens-\\nburg`` -> ``Flensburg`` (lower-case continuation: drop the hyphen)
    ``Risum-\\nLindholm`` -> ``Risum-Lindholm`` (capitalised: it is a real hyphen)
    """
    text = re.sub(r"(\w)-[ \t]*\n[ \t]*(?=[a-zäöüß])", r"\1", text)
    text = re.sub(r"(\w-)[ \t]*\n[ \t]*(?=[A-ZÄÖÜ])", r"\1", text)
    return text


# ----------------------------------------------------------------------------
# section / entry patterns
# ----------------------------------------------------------------------------

_REGISTER_KIND = r"(?:HRA|HRB|GnR|PR|VR)"
_HR_NUMBER = rf"{_REGISTER_KIND}[ \t]*\d+(?:[ \t]+[A-ZÄÖÜ]{{1,3}})?"

# An amount plus its ISO-ish currency code. The AD prints pre-euro registrations in
# DEM, so the currency must never be hard-coded to EUR.
_MONEY_RE = re.compile(
    r"(?<![\d.,])(?P<zahl>\d[\d.]*,\d{2})[ \t]*(?P<waehrung>[A-ZÄÖÜ]{2,3})(?![A-Za-zÄÖÜäöüß])"
)

# "Enleni GmbH, Behrendorf (Amtsgericht Flensburg, HRB 10342 FL)"
_ORG_ENTRY_RE = re.compile(
    rf"^(?P<name>[^,()]+?)\s*,\s*"
    rf"(?P<ort>[^,()]+?)\s*"
    rf"\(\s*(?:(?P<court>[^,()]*?)\s*,\s*)?(?P<hr>{_HR_NUMBER})\s*\)"
)

# "Andresen, Heike Susann, *19.11.1974, Jübek"  /  "Becker, Herta, Köln, *11.05.1957"
_PERSON_ENTRY_RE = re.compile(
    r"^(?P<nachname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-' ]*?)\s*,\s*"
    r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-'. ]*?)\s*,\s*"
    r"(?P<rest>.+)$"
)

_DOB_RE = re.compile(r"\*\s*(\d{2}\.\d{2}\.\d{4})")

# Start of the "persönlich haftende Gesellschafter" block. Either the numbered
# heading (Aktueller Ausdruck) or the inline label (older Ausdruck layout).
_PHG_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:\d+[.\s]*)?b\)[ \t]*Inhaber[^\n]*\n"
    r"|Persönlich haftende[rn]?\s+Gesellschafter(?:in)?[^:\n]*:[ \t]*"
)

_KOMM_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:\d+[.\s]*)?c\)[ \t]*Kommanditist[^\n]*\n"
    r"|Kommanditist(?:\(en\)|en)?[^:\n]*:[ \t]*"
)

# A new numbered/lettered heading ends the current block.
_HEADING_RE = re.compile(r"(?m)^[ \t]*(?:\d+\.[ \t]*[a-z]?\)|[a-z]\)|\d+\.[ \t]+[A-ZÄÖÜ])")

# A bare list index such as "5." on its own line, or "5. " before the entry.
_INDEX_ONLY_RE = re.compile(r"^\d{1,3}\.$")
_INDEX_PREFIX_RE = re.compile(r"^\d{1,3}\.[ \t]+(?=\S)")

# The older layout repeats the role label in front of every record, so the label is
# both an entry boundary and noise that has to come off before parsing.
_ROLE_LABEL_RE = re.compile(
    r"^(?:Persönlich haftende[rn]?\s+Gesellschafter(?:in)?"
    r"|Inhaber(?:in)?"
    r"|Kommanditist(?:\(en\)|en|in)?"
    r"|Geschäftsführer(?:in)?"
    r"|Prokurist(?:\(en\)|en|in)?"
    r"|Vorstand|Mitglied(?:er)?)[^:\n]*:[ \t]*"
)


def _block_after(text: str, marker: re.Pattern, stop_at: int | None = None) -> str:
    """Return the text between ``marker`` and the next heading (or ``stop_at``)."""
    m = marker.search(text)
    if not m:
        return ""

    start = m.end()
    end = len(text)

    heading = _HEADING_RE.search(text, start)
    if heading:
        end = heading.start()

    if stop_at is not None and start < stop_at < end:
        end = stop_at

    return text[start:end]


def _group_entries(block: str) -> list[str]:
    """
    Split a register block into one string per entry.

    Handles the enumerated AD layout (``5.`` on its own line) as well as the older
    unnumbered layout, and keeps wrapped continuation lines with their entry.
    """
    entries: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            entries.append("\n".join(current))
            current.clear()

    for raw in block.split("\n"):
        line = raw.strip()
        if not line:
            continue

        if _INDEX_ONLY_RE.match(line):
            flush()
            continue

        prefix = _INDEX_PREFIX_RE.match(line) or _ROLE_LABEL_RE.match(line)
        if prefix:
            flush()
            line = line[prefix.end():].strip()
            if not line:
                continue

        if current and not _is_continuation(current, line):
            flush()

        current.append(line)

    flush()
    return entries


def _is_continuation(current: list[str], line: str) -> bool:
    """Decide whether ``line`` wraps the entry accumulated in ``current``."""
    joined = "\n".join(current)

    # An unclosed "(Amtsgericht ..." always continues on the next line, and a line
    # opening with "(" is the court reference wrapped away from its company name.
    if joined.count("(") > joined.count(")"):
        return True

    if line.startswith("("):
        return True

    # Hyphenated word break at the end of the previous line.
    if re.search(r"\w-[ \t]*$", _MONEY_RE.sub(" ", current[-1])):
        return True

    # A line that itself parses as a full entry starts a new one.
    if _ORG_ENTRY_RE.match(line) or _PERSON_ENTRY_RE.match(line):
        return False

    # Otherwise a lower-case opener is wrapped prose, an amount belongs to the
    # entry above, and anything else starts a new record.
    if line[:1].islower():
        return True

    return bool(_MONEY_RE.match(line))


def _take_money(entry: str) -> tuple[str, str, str]:
    """Pull the Hafteinlage out of an entry, returning (rest, share, currency)."""
    m = _MONEY_RE.search(entry)
    if not m:
        return entry, "", ""

    rest = entry[:m.start()] + " " + entry[m.end():]
    return rest, _german_money_to_en(m.group("zahl")), m.group("waehrung")


def _flatten(entry: str) -> str:
    """Collapse a multi-line entry into a single normalised line."""
    return _norm(re.sub(r"\s*\n\s*", " ", _dehyphenate(entry)))


def _court_city(raw: str) -> str:
    """"Amtsgericht Flensburg" -> "Flensburg"."""
    city = re.sub(r"^(?:Amtsgericht|Amtsgerichts|AG)\s+", "", _norm(raw))
    return city.strip(" ,")


def _parse_org(line: str) -> dict | None:
    m = _ORG_ENTRY_RE.match(line)
    if not m:
        return None

    return {
        "name": _norm(m.group("name")),
        "ort": _norm(m.group("ort")),
        "hr": re.sub(r"\s+", " ", _norm(m.group("hr"))),
        "court": _court_city(m.group("court") or ""),
    }


def _parse_person(line: str) -> dict | None:
    m = _PERSON_ENTRY_RE.match(line)
    if not m:
        return None

    rest = m.group("rest")
    dob = ""

    dm = _DOB_RE.search(rest)
    if dm:
        dob = _to_iso_date(dm.group(1))
        rest = rest[:dm.start()] + " " + rest[dm.end():]

    ort = _norm(rest).strip(" ,")
    if not re.fullmatch(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-./ ]*", ort or "x"):
        ort = ""

    return {
        "nachname": _norm(m.group("nachname")),
        "vorname": _norm(m.group("vorname")),
        "ort": ort,
        "geburtsdatum": dob,
    }


# ----------------------------------------------------------------------------
# main entry point
# ----------------------------------------------------------------------------

def parse_handelsregister_a_text(text: str) -> dict:
    """
    Parse text from a German Handelsregister A PDF into the target JSON schema.

    Understands both printout layouts:
    - "Ausdruck" with inline labels ("Nummer der Firma:", "Kommanditist(en):")
    - "Aktueller Ausdruck" with numbered headings ("2.a) Firma", "c) Kommanditisten")
    """
    out = deepcopy(BASE_OUTPUT)
    t = _prepare(text)

    # Section parsing runs on a copy without the repeated page furniture; the
    # company-level fields below are read from the full text.
    body = _PAGE_NOISE_RE.sub("", t)

    # ------------------------------------------------------------
    # 1) Court and main register number
    # ------------------------------------------------------------
    m_court = re.search(
        r"Amtsgerichts?\s+([A-Za-zÄÖÜäöüß\- ]+?)(?=\n|,|$)",
        t,
    )

    if not m_court:
        m_court = re.search(
            r"Amtsgerichts?\s+([A-Za-zÄÖÜäöüß\- ]+)",
            t,
        )

    if m_court:
        court_city = _norm(m_court.group(1))

        # avoid accidentally capturing too much text
        court_city = re.split(
            r"\s+(Abteilung|Abt\.|Wiedergabe|Nummer|Abdruck|des|HRA|HRB)\b",
            court_city,
        )[0].strip()

        out["unternehmen"]["registergericht"] = court_city
        out["unternehmen"]["amtsgericht_verbatim"] = f"Amtsgericht {court_city}"

    # HR number (label form first, then bare-header fallback).
    # The court suffix ("HRA 8195 FL") is part of the number, but only when it is a
    # standalone token on the same line — otherwise "HRA 18706\nAllgemeine ..." would
    # swallow the "A" of the following heading.
    m_hr = re.search(
        rf"Nummer der Firma:[ \t]*({_HR_NUMBER})(?![A-Za-zÄÖÜäöüß])",
        t,
    )
    if not m_hr:
        m_hr = re.search(rf"\b({_HR_NUMBER})(?![A-Za-zÄÖÜäöüß])", t)
    if m_hr:
        hr_number = re.sub(r"\s+", " ", _norm(m_hr.group(1)))
        out["unternehmen"]["handelsregisternummer"] = hr_number

    # ------------------------------------------------------------
    # 2) Company name, section 2a
    # ------------------------------------------------------------
    m_name = re.search(
        r"2\.\s*a\)\s*Firma:?\s*(.+?)(?=\n\s*b\)|\s*b\)\s*Sitz|\s*b\))",
        t,
        re.S | re.I,
    )

    if m_name:
        company_name = _norm(m_name.group(1))

        out["unternehmen"]["name"] = company_name
        out["unternehmen"]["adresse"]["nameKomplett"] = company_name
        out["unternehmen"]["rechtsform"] = _legal_form_number(company_name)

    # ------------------------------------------------------------
    # 3) Seat and business address, section 2b
    # ------------------------------------------------------------
    m_sitz = re.search(
        r"b\)\s*Sitz[^\n]*\n[ \t]*(?P<ort>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-/ ]*?)[ \t]*(?=\n)",
        t,
    )
    if not m_sitz:
        m_sitz = re.search(
            r"b\)\s*Sitz.*?:\s*(?P<ort>[A-Za-zÄÖÜäöüß\- ]+?)\s*(?:Geschäftsanschrift:|\n)",
            t,
            re.S,
        )

    if m_sitz:
        city = _norm(m_sitz.group("ort"))

        out["unternehmen"]["adresse"]["ort"] = city
        out["unternehmen"]["adresse"]["bundesland"] = _get_bundesland(city)

    # Street line. House numbers may be ranges ("31 - 41") or carry a letter ("7a"),
    # so a plain \d+\w? is not enough.
    addr_scope = t
    m_anchor = re.search(r"(?:Geschäftsanschrift:|b\)\s*Sitz)", t)
    if m_anchor:
        addr_scope = t[m_anchor.end():]

    m_addr = re.search(
        r"(?m)^[ \t]*(?P<strasse>[^\n,\d][^\n,]*?)[ \t]+"
        r"(?P<hausnummer>\d+[a-zA-Z]?(?:[ \t]*[-–/][ \t]*\d+[a-zA-Z]?)?)[ \t]*,[ \t]*"
        r"(?P<plz>\d{5})[ \t]+(?P<ort>[^\n,]+?)[ \t]*$",
        addr_scope,
    )

    if m_addr:
        street = _norm(m_addr.group("strasse"))
        house_number = _norm(m_addr.group("hausnummer"))
        post_code = _norm(m_addr.group("plz"))
        city = _norm(m_addr.group("ort"))

        out["unternehmen"]["adresse"]["strasse"] = street
        out["unternehmen"]["adresse"]["hausnummer"] = house_number
        out["unternehmen"]["adresse"]["plz"] = post_code
        out["unternehmen"]["adresse"]["ort"] = city

        if not out["unternehmen"]["adresse"]["bundesland"]:
            out["unternehmen"]["adresse"]["bundesland"] = _get_bundesland(city, post_code)

    # ------------------------------------------------------------
    # 4) Legal form, beginning date, last change, retrieval date
    # ------------------------------------------------------------
    if not out["unternehmen"]["rechtsform"]:
        m_form = re.search(
            r"\d\.\s*a\)\s*Rechtsform[^\n:]*:?\s*(?:\n[ \t]*)?(.+?)\s*(?=\n|Beginn:)",
            t,
            re.I,
        )
        if m_form:
            out["unternehmen"]["rechtsform"] = _legal_form_number(_norm(m_form.group(1)))

    if EMIT_REGISTRATION_DATES:
        m_begin = re.search(r"Beginn:?\s*(\d{2}\.\d{2}\.\d{4})", t)
        if m_begin:
            out["unternehmen"]["eintragungsdatum"] = _to_iso_date(m_begin.group(1))

        m_last = re.search(
            r"Tag der letzten Eintragung:?\s*(?:\n[ \t]*)?(\d{2}\.\d{2}\.\d{4})",
            t,
        )
        if m_last:
            out["unternehmen"]["letzte_aenderung"] = _to_iso_date(m_last.group(1))

    abruf_dates = re.findall(r"Abruf vom\s+(\d{2}\.\d{2}\.\d{4})", t)

    # ------------------------------------------------------------
    # 5) Personally liable partners / PHGs
    # ------------------------------------------------------------
    komm_marker = _KOMM_MARKER_RE.search(body)
    phg_block = _block_after(
        body,
        _PHG_MARKER_RE,
        stop_at=komm_marker.start() if komm_marker else None,
    )

    for entry in _group_entries(phg_block):
        entry, _share, _waehrung = _take_money(entry)
        line = _flatten(entry)

        org = _parse_org(line)
        if org:
            _add_org(
                out["persoenlich_haftende_gesellschafter"],
                org,
                out["unternehmen"]["registergericht"],
            )
            continue

        person = _parse_person(line)
        if person:
            _add_natural_phg(out["natuerliche_phGs"], person)

    # ------------------------------------------------------------
    # 6) Limited partners / Kommanditisten
    #    e.g. "Andresen, Heike Susann, *19.11.1974, Jübek   4.000,00 EUR"
    #         "iTerra Wind GmbH & Co. KG, Risum-Lindholm (Amtsgericht Flensburg,
    #          HRA 7709 FL)                                192.000,00 EUR"
    # ------------------------------------------------------------
    komm_block = _block_after(body, _KOMM_MARKER_RE)
    komm_highlights: list[tuple[str, str]] = []

    for entry in _group_entries(komm_block):
        entry, share, waehrung = _take_money(entry)
        line = _flatten(entry)

        org = _parse_org(line)
        if org:
            record = _add_org(
                out["kommanditisten_gesellschaften"],
                org,
                out["unternehmen"]["registergericht"],
            )
            if record is not None:
                record["beteiligung"] = {"share": share or 0, "waehrung": waehrung}
                komm_highlights.append(("org", record))
            continue

        person = _parse_person(line)
        if person:
            record = _add_kommanditist(out["kommanditisten_personen"], person, share, waehrung)
            if record is not None:
                komm_highlights.append(("person", record))

    # ------------------------------------------------------------
    # 7) to_highlight — emitted in the same order as the XML parser
    # ------------------------------------------------------------
    address = out["unternehmen"]["adresse"]
    if address["strasse"] and address["plz"]:
        _append_hl(
            out,
            f"{address['strasse']} {address['hausnummer']}, {address['plz']} {address['ort']}",
        )

    if out["unternehmen"]["registergericht"]:
        _append_hl(out, f"Amtsgerichts {out['unternehmen']['registergericht']}")

    _append_hl(out, out["unternehmen"]["handelsregisternummer"])
    _append_hl(out, out["unternehmen"]["name"])
    _append_hl(out, address["ort"])

    for phg in out["persoenlich_haftende_gesellschafter"]:
        if phg["registergericht"]:
            _append_hl(out, f"Amtsgericht {phg['registergericht']}")
        _append_hl(out, phg["handelsregisternummer"])
        _append_hl(out, phg["name"])
        _append_hl(out, phg["adresse"]["ort"])

    for kind, record in komm_highlights:
        if kind == "org":
            if record["registergericht"]:
                _append_hl(out, f"Amtsgericht {record['registergericht']}")
            _append_hl(out, record["handelsregisternummer"])
            _append_hl(out, record["name"])
            _append_hl(out, record["adresse"]["ort"])
        else:
            _append_hl(out, record["adresse"]["nameKomplett"])

        share = record["beteiligung"].get("share")
        waehrung = record["beteiligung"].get("waehrung")
        if share and waehrung:
            _append_hl(out, f"{share} {waehrung}")

    if abruf_dates:
        _append_hl(out, f"Abruf vom {abruf_dates[-1]}")

    return out


def _add_org(bucket: list, org: dict, fallback_court: str) -> dict | None:
    """Append an organisation record unless an identical one is already present."""
    name = org["name"]
    hr_number = org["hr"]

    for existing in bucket:
        if existing["name"] == name and existing["handelsregisternummer"] == hr_number:
            return None

    record = {
        "name": name,
        "handelsregisternummer": hr_number,
        "rechtsform": _legal_form_number(name),
        "registergericht": org["court"] or fallback_court,
        "amtsgericht_verbatim": "",
        "adresse": {
            "nameKomplett": name,
            "strasse": "",
            "hausnummer": "",
            "plz": "",
            "ort": org["ort"],
            "bundesland": "",  # XML leaves the Bundesland of a partner company empty
            "land": "DE",
        },
        "beteiligung": {
            "share": 0,
        },
    }
    bucket.append(record)
    return record


def _add_natural_phg(bucket: list, person: dict) -> dict | None:
    full_name = _norm(f"{person['vorname']} {person['nachname']}")

    for existing in bucket:
        if existing["adresse"]["nameKomplett"] == full_name:
            return None

    record = {
        "name": "",
        "handelsregisternummer": "",
        "geburtsdatum": person["geburtsdatum"],
        "geb_name": "",
        "adresse": {
            "nameKomplett": full_name,
            "strasse": "",
            "hausnummer": "",
            "plz": "",
            "ort": person["ort"],
            "bundesland": _get_bundesland(person["ort"]),
            "land": "DE",
        },
        "beteiligung": {"share": 0},
        "vorname": person["vorname"],
        "nachname": person["nachname"],
    }
    bucket.append(record)
    return record


def _add_kommanditist(bucket: list, person: dict, share: str, waehrung: str) -> dict | None:
    full_name = _norm(f"{person['vorname']} {person['nachname']}")

    for existing in bucket:
        if existing["adresse"]["nameKomplett"] == full_name:
            return None

    record = {
        "name": "",
        "handelsregisternummer": "",
        "geburtsdatum": person["geburtsdatum"] or None,
        "geb_name": "",
        "adresse": {
            "nameKomplett": full_name,
            "strasse": "",
            "hausnummer": "",
            "plz": "",
            "ort": person["ort"],
            "bundesland": "",  # XML leaves the Bundesland of a Kommanditist empty
            "land": "DE",
        },
        "beteiligung": {"share": share, "waehrung": waehrung},
        "vorname": person["vorname"],
        "nachname": person["nachname"],
    }
    bucket.append(record)
    return record


def parse_handelsregister_a_or_none(text: str) -> dict | None:
    """
    Return parsed output only if the minimum required company fields were found.
    Otherwise return None.
    """
    out = parse_handelsregister_a_text(text)

    company = out.get("unternehmen", {})
    missing = [
        field
        for field in ("name", "handelsregisternummer", "registergericht")
        if not company.get(field)
    ]

    if missing:
        log.warning("HRA parser missing fields %s; parsed company: %s", missing, company)
        return None

    return out
