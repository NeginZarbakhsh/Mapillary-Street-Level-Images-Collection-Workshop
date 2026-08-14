import html
import re
from copy import deepcopy
from datetime import datetime

from kyc_assistant.domain.legal_forms import extract_rechtsform, rechtsform_map

# Shared Bundesland dataset (same source as xml_parser) — adjust path if needed
try:
    from kyc_assistant.services.xml_parser import get_bundesland_data
except Exception:
    get_bundesland_data = None

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
    "Behrendorf": "Schleswig-Holstein",
    "Haselund": "Schleswig-Holstein",
    "Husum": "Schleswig-Holstein",
    "Hattstedt": "Schleswig-Holstein",
    "Jübek": "Schleswig-Holstein",
    "Klixbüll": "Schleswig-Holstein",
    "Großenwiehe": "Schleswig-Holstein",
    "Struckum": "Schleswig-Holstein",
    "Schacht-Audorf": "Schleswig-Holstein",
    "Köln": "Nordrhein-Westfalen",
    "Brühl": "Nordrhein-Westfalen",
    "Farnstädt": "Sachsen-Anhalt",
    "Stuttgart": "Baden-Württemberg",
}

# Foreign residents print as "Ort / Land" (e.g. "Luxemburg / Luxemburg"); the XML
# side reports an ISO code, so the German country name has to map onto one.
COUNTRY_TO_ISO = {
    "Deutschland": "DE", "Luxemburg": "LU", "Schweiz": "CH", "Österreich": "AT",
    "Niederlande": "NL", "Belgien": "BE", "Frankreich": "FR", "Dänemark": "DK",
    "Italien": "IT", "Spanien": "ES", "Portugal": "PT", "Großbritannien": "GB",
    "Vereinigtes Königreich": "GB", "Irland": "IE", "Polen": "PL",
    "Tschechien": "CZ", "Liechtenstein": "LI", "Schweden": "SE",
    "Norwegen": "NO", "Finnland": "FI", "Ungarn": "HU", "Griechenland": "GR",
    "Türkei": "TR", "Kanada": "CA", "USA": "US",
    "Vereinigte Staaten": "US", "Monaco": "MC",
}

# Register references, incl. the court suffix some courts print ("HRA 8195 FL").
_HR_NUMBER = r"(?:HRA|HRB|GnR|PR|VR)\s*\d+(?:\s+[A-ZÄÖÜ]{1,3})?"

# A company entry is recognised by its register parenthetical, not by a legal-form
# suffix in the name — an "AG", "e.K." or "Stiftung & Co. KG" is matched just as
# well as a GmbH.
_ORG_WITH_REGISTER = re.compile(
    r"(?P<name>[^,():]+?)\s*,\s*"
    r"(?P<ort>[^,():]+?)\s*"
    r"\(\s*(?:(?P<court>(?:Amtsgericht|AG)\s+[^,()]*?)\s*,?\s*)?"
    rf"(?P<hr>{_HR_NUMBER})\s*\)"
)

# A natural person: surname, given names, town and birth date. The date sits on
# either side of the town depending on layout, and the town may carry "/ Land".
# The two orders are spelled out as alternatives rather than making the date
# optional — an optional trailing date lets the town group match a single letter
# and the entry then looks like a person with no birth date.
_DATE = r"\d{2}\.\d{2}\.\d{4}"
_PLACE = r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-./ ]*?"
# German civil-register surnames sometimes carry a lower-case particle
# ("von Bülow", "van der Berg", "zu Guttenberg"). Without this, the particle
# fails to match (it's followed by a space, not the comma the pattern needs
# next) and the surname is silently truncated to whatever follows it.
_SURNAME_PARTICLE = r"(?:(?:von|van|de|zu|zur|zum|di|la|le|del|der)\s+)*"

# A married name is printed as "Speer, Silke, geb. Geffe, <town>, *<date>".
# The segment sits between the given name and the town, so without it the
# pattern breaks and — worse — a later match can read the birth name itself
# as somebody's surname.
_NAME_HEAD = (
    rf"(?P<nachname>{_SURNAME_PARTICLE}[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-']*)\s*,\s*"
    r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-'. ]*?)\s*,\s*"
    r"(?:geb\.?\s*(?P<geburtsname>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-']*)\s*,\s*)?"
)

# Birth date required — used where nothing else anchors the end of the entry.
_PERSON_WITH_DOB = _NAME_HEAD + (
    r"(?:"
    rf"\*(?P<dob1>{_DATE})\s*,?\s*(?P<ort1>{_PLACE})"
    r"|"
    rf"(?P<ort2>{_PLACE})\s*,?\s*\*(?P<dob2>{_DATE})"
    r")"
)

# Birth date optional — only safe where a Hafteinlage follows and anchors the end.
_PERSON_ANY = _NAME_HEAD + (
    r"(?:"
    rf"\*(?P<dob1>{_DATE})\s*,?\s*(?P<ort1>{_PLACE})"
    r"|"
    rf"(?P<ort2>{_PLACE})\s*,?\s*\*(?P<dob2>{_DATE})"
    r"|"
    rf"(?P<ort3>{_PLACE})"
    r")"
)

# The town after a postal code ends at the next section, not at a fixed word count.
_ADDR_STOP = (
    r"(?=\s+(?:\d+\s*\.|[a-z]\)|Persönlich|Prokura|Kommanditist|Geschäftsanschrift"
    r"|Gegenstand|Allgemeine|Inhaber|Rechtsform|Sonstige|Tag\s+der|Abruf|Vertretung)"
    r"|\s*$)"
)
_ADDR_ORT = (
    r"(?P<ort>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-/]*"
    r"(?:\s+[A-Za-zÄÖÜäöüß.][A-Za-zÄÖÜäöüß\-/.]*){0,3}?)"
)


def _norm(value: str) -> str:
    """
    Normalise text:
    - convert HTML entities like &amp; to &
    - collapse spaces/tabs
    - strip leading/trailing whitespace
    """
    value = html.unescape(value or "")
    return re.sub(r"[ \t]+", " ", value).strip()


def _normalize_obj(obj):
    if isinstance(obj, str):
        return obj.strip()

    if isinstance(obj, list):
        return [_normalize_obj(x) for x in obj]

    if isinstance(obj, dict):
        return {k: _normalize_obj(v) for k, v in obj.items()}

    return obj


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


def _clean_company_name(name: str) -> str:
    name = _norm(name)
    # Strip leading Haftsumme/Einlage fragments: "15.000.000,00 EUR", "00 EUR", "192.000,00 DEM 5."
    name = re.sub(r"^[\d.,]*\s*(?:EUR|DEM)\s*\d*\.?\s*", "", name).strip()
    name = re.sub(r"^\d+\.\s*", "", name).strip()
    # Strip a leading list index or role label left over from the printout.
    name = re.sub(
        r"^(?:Persönlich haftende[rn]?\s+Gesellschafter(?:in)?|Kommanditist(?:\(en\)|en|in)?)\s*:\s*",
        "",
        name,
    ).strip()
    name = re.sub(r",?\s*Gesellschaft mit beschränkter Haftung.*$", "", name).strip()
    return name


def _append_hl(out: dict, value: str) -> None:
    """
    Append a value to to_highlight only if it is non-empty and not duplicated.
    """
    value = _norm(value)

    if value and value not in out["to_highlight"]:
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


def _split_ort_land(raw_ort: str) -> tuple[str, str]:
    """
    Split a printed residence into town and ISO country code.

    "Luxemburg / Luxemburg" -> ("Luxemburg", "LU")
    "Dinslaken"             -> ("Dinslaken", "DE")

    An unknown country yields "" rather than "DE", so it surfaces as a mismatch
    instead of being silently wrong.
    """
    raw = _norm(raw_ort)

    if "/" in raw:
        city, country = (part.strip() for part in raw.split("/", 1))
        return city, COUNTRY_TO_ISO.get(country, "")

    return raw, "DE"


def _court_city(raw: str) -> str:
    """"Amtsgericht Flensburg" -> "Flensburg"."""
    return _norm(re.sub(r"^(?:Amtsgericht|Amtsgerichts|AG)\s+", "", _norm(raw))).strip(" ,")


def _person_from_match(m) -> tuple[str, str, str, str, str]:
    """(first, last, city, land, iso_dob) from a match using _PERSON_CORE."""
    last = _norm(m.group("nachname"))
    last = re.sub(r"^Dr\.\s*", "", last).strip()
    first = _norm(m.group("vorname"))
    groups = m.groupdict()
    raw_ort = groups.get("ort1") or groups.get("ort2") or groups.get("ort3") or ""
    city, land = _split_ort_land(raw_ort)
    dob_raw = groups.get("dob1") or groups.get("dob2")
    return first, last, city, land, (_to_iso_date(dob_raw) if dob_raw else "")


def _geburtsname(m) -> str:
    """The 'geb. X' birth name, when the entry printed one."""
    return _norm(m.groupdict().get("geburtsname") or "")


def _person_record(first, last, city, land, dob, bundesland, beteiligung,
                   geburtsname: str = "") -> dict:
    """One person, in the shared schema shape.

    ``geburtsname`` is added as a key only when the document printed one: the
    XML side omits the field entirely otherwise, and an always-present key
    would make every ordinary person record differ.
    """
    record = {
        "name": "",
        "handelsregisternummer": "",
        "geburtsdatum": dob,
        "geb_name": "",
        "adresse": {
            "nameKomplett": _norm(f"{first} {last}"),
            "strasse": "",
            "hausnummer": "",
            "plz": "",
            "ort": city,
            "bundesland": bundesland,
            "land": land,
        },
        "beteiligung": beteiligung,
        "vorname": first,
        "nachname": last,
    }

    if geburtsname:
        record["geburtsname"] = geburtsname

    return record


def _org_record(name, ort, hr_number, court_city, fallback_court, beteiligung) -> dict:
    return {
        "name": name,
        "handelsregisternummer": hr_number,
        "rechtsform": _legal_form_number(name),
        "registergericht": court_city or fallback_court,
        "amtsgericht_verbatim": "",
        "adresse": {
            "nameKomplett": name,
            "strasse": "",
            "hausnummer": "",
            "plz": "",
            "ort": ort,
            "bundesland": _get_bundesland(ort),
            "land": "DE",
        },
        "beteiligung": beteiligung,
    }


# ---------------------------------------------------------------------------
# Section-boundary patterns and the money pattern.
#
# These live at module level, not inside the parse function, so that
# section_blocks() below uses exactly the same patterns the parser itself does.
# A separate copy would drift and make a diagnostic report the wrong cause.
# ---------------------------------------------------------------------------

# The Kommanditisten heading carries a colon in the older "Ausdruck" layout
# ("Kommanditist(en):") but NOT in the "Aktueller Ausdruck" layout, where it is
# printed bare ("c) Kommanditisten, Mitglieder"). Requiring the colon skipped
# the whole section, and every Kommanditist in it, on colon-less printouts.
_KOMM_SECTION_RE = re.compile(
    r"(?:"
    r"Kommanditist(?:en|\(en\))?(?:\s*,\s*Mitglieder)?\s*:"
    r"|(?:\d+\s*\.\s*)?[a-z]\)\s*Kommanditisten(?:\s*,\s*Mitglieder)?\s*:?"
    r")\s*(?P<block>.*?)"
    r"(?=\s*\d+\.\s*[a-z]?\)?\s*Tag der letzten Eintragung|\s*Abruf vom|\Z)",
    re.S,
)

_PHG_MARKER_RE = re.compile(
    r"(?:Persönlich haftende[rn]?\s+Gesellschafter(?:in)?\s*:|b\)\s*Inhaber)"
)

# What ends the partners section. A "4. Prokura:" block commonly sits between
# the partners and the Kommanditisten; without this the Prokuristen would be
# read as partners.
_NEXT_SECTION_RE = re.compile(
    r"Prokura\s*:"
    r"|Kommanditist"
    r"|Rechtsform"
    r"|Sonstige\s+Rechtsverhältnisse"
    r"|Tag\s+der\s+letzten\s+Eintragung"
    r"|Abruf\s+vom"
)

_PROKURA_SECTION_RE = re.compile(
    r"\d+\.\s*Prokura\s*:?\s*(?P<block>.*?)"
    r"(?=\s*\d+\.\s*a\)|\s*\d+\.\s+[A-ZÄÖÜ]|\Z)",
    re.S | re.I,
)

# A capital contribution, under any of its printed labels or none at all, in
# any currency (pre-euro registrations print DEM).
_MONEY = (
    r"(?:(?:Haft(?:summe|einlage)|Einlage|Kapitalanteil|Kommanditeinlage)\s*:\s*)?"
    r"(?P<share>[\d.]+,\d{2})\s*(?P<currency>[A-ZÄÖÜ]{2,3})\b"
)


def normalise_for_parsing(text: str) -> str:
    """The exact text transform the parser applies before any matching."""
    text = html.unescape(text or "")
    text = text.replace("**", " ")
    return re.sub(r"\s+", " ", text)


def section_blocks(text: str) -> dict:
    """Locate every section block, the same way parse_handelsregister_a_text does.

    Returns the normalised text plus each block's content and character span.
    This is what lets a diagnostic answer the question that actually matters
    when an entry goes missing: was the line even inside the block the pattern
    searches, or did the pattern never see it?
    """
    t = normalise_for_parsing(text)

    komm_match = _KOMM_SECTION_RE.search(t)
    komm_start = komm_match.start() if komm_match else len(t)

    phg_marker = _PHG_MARKER_RE.search(t)

    phg_end = komm_start
    if phg_marker:
        boundary = _NEXT_SECTION_RE.search(t, phg_marker.end())
        if boundary and boundary.start() < phg_end:
            phg_end = boundary.start()

    phg_start = phg_marker.start() if phg_marker else -1
    prokura_match = _PROKURA_SECTION_RE.search(t)

    return {
        "text": t,
        "komm_block": komm_match.group("block") if komm_match else "",
        "komm_span": komm_match.span("block") if komm_match else (-1, -1),
        "phg_block": t[phg_start:phg_end] if phg_marker else "",
        "phg_span": (phg_start, phg_end),
        "prokura_block": prokura_match.group("block") if prokura_match else "",
        "prokura_span": prokura_match.span("block") if prokura_match else (-1, -1),
    }


def parse_handelsregister_a_text(text: str) -> dict:
    """
    Parse text from a German Handelsregister A PDF into the target JSON schema.

    Handles both printout layouts:
    - "Ausdruck" with inline labels ("Nummer der Firma:", "Kommanditist(en):")
    - "Aktueller Ausdruck" with numbered headings ("2. a) Firma", "c) Kommanditisten")

    ------------------------------------------------------------------------
    MAP: PDF SECTION  ->  WHAT THIS FUNCTION DOES WITH IT
    ------------------------------------------------------------------------
    Open an AD printout beside this file. Every numbered heading in the
    document maps to exactly one labelled block below, in the same order.

      page header / "Nummer der Firma:"
                             -> unternehmen.registergericht
                                unternehmen.handelsregisternummer   [STEP 1]

      1.    Anzahl der bisherigen Eintragungen
                             -> NOT EXTRACTED (no field in the schema)

      2. a) Firma            -> unternehmen.name, .rechtsform        [STEP 2]
         b) Sitz, Niederlassung, inlaendische Geschaeftsanschrift
                             -> unternehmen.adresse.*                [STEP 3]
         c) Gegenstand des Unternehmens
                             -> NOT EXTRACTED (no field in the schema)

      3. a) Allgemeine Vertretungsregelung
                             -> NOT EXTRACTED (no field in the schema)
         b) Inhaber, persoenlich haftende Gesellschafter, ...
                             -> persoenlich_haftende_gesellschafter  [STEP 5]
                                natuerliche_phGs                     [STEP 5b]

      4.    Prokura          -> prokuristen                          [STEP 4b]

      5. a) Rechtsform, Beginn und Satzung
                             -> unternehmen.rechtsform (fallback)    [STEP 4]
         b) Sonstige Rechtsverhaeltnisse
                             -> NOT EXTRACTED (no field in the schema)
         c) Kommanditisten, Mitglieder
                             -> kommanditisten_personen              [STEP 6]
                                kommanditisten_gesellschaften        [STEP 6]

      6. a) Tag der letzten Eintragung
                             -> parsed, then deliberately blanked    [STEP 4]

    The older "Ausdruck" layout numbers its sections differently (e.g.
    Kommanditisten may sit under "4." instead of "5. c)"), which is why each
    block below matches on the heading TEXT, never on the number.

    Fields never written by any block, whatever the PDF says:
      leitende_personen, board, company_owner, vertreter,
      persoenlich_haftende_gesellschafter_ohne_vertretung,
      natuerliche_phGs_ohne_vertretung, unternehmen.geschaeftsfuehrer
    ------------------------------------------------------------------------
    """
    out = deepcopy(BASE_OUTPUT)
    text = html.unescape(text or "")
    t = text
    # Clean PDF formatting markers
    t = t.replace("**", " ")
    t = re.sub(r"\s+", " ", t)

    # ==========================================================================
    # STEP 0 - LOCATE SECTION BOUNDARIES (no extraction happens here)
    #
    # Finds where each PDF section starts and stops, so that later blocks
    # only ever read inside their own section. Without this, a partner's
    # own register number could be taken as the company's, and the
    # Prokura people (PDF 4.) could be counted as partners (PDF 3. b).
    #
    #   komm_block -> text inside PDF  5. c) Kommanditisten, Mitglieder
    #   phg_block  -> text inside PDF  3. b) Inhaber, persoenlich haft...
    # ==========================================================================
    # The heading carries a colon in the older "Ausdruck" layout
    # ("Kommanditist(en):") but NOT in the "Aktueller Ausdruck" layout, where
    # it is a numbered heading printed bare ("c) Kommanditisten, Mitglieder").
    # Requiring the colon skipped the entire section — and therefore every
    # Kommanditist in the document — on every colon-less printout, silently.
    # Hence two alternatives: labelled-with-colon, or lettered heading.
    komm_match = _KOMM_SECTION_RE.search(t)
    komm_block = komm_match.group("block") if komm_match else ""
    komm_start = komm_match.start() if komm_match else len(t)

    phg_marker = _PHG_MARKER_RE.search(t)
    head = t[: phg_marker.start()] if phg_marker else t[:komm_start]

    # Full span covering both partner sections, so a register number that only
    # appears inside a partner's own entry is never mistaken for the
    # company's. Computed once here; section 5 reuses phg_end for its block.
    phg_end = komm_start
    if phg_marker:
        boundary = _NEXT_SECTION_RE.search(t, phg_marker.end())
        if boundary and boundary.start() < phg_end:
            phg_end = boundary.start()

    komm_end = komm_match.end() if komm_match else komm_start
    outside_partners = (
        (t[:phg_marker.start()] if phg_marker else t[:komm_start])
        + " "
        + t[max(phg_end, komm_start):komm_start]  # rarely non-empty; kept for safety
        + " "
        + t[komm_end:]
    )

    # ==========================================================================
    # STEP 1 - PDF page header, and/or the line 'Nummer der Firma:'
    #
    # Reads : the court name, and the company's OWN register number
    # Writes: unternehmen.registergericht
    #         unternehmen.amtsgericht_verbatim
    #         unternehmen.handelsregisternummer
    #
    # The number is looked for in three places, best first, so that a
    # partner company's HRB (printed later, in PDF 3. b) can never be
    # mistaken for this company's own number.
    # ==========================================================================
    m_court = re.search(r"Amtsgerichts?\s+([A-Za-zÄÖÜäöüß\- ]+?)(?=\n|,|$)", t)

    if not m_court:
        m_court = re.search(r"Amtsgerichts?\s+([A-Za-zÄÖÜäöüß\- ]+)", t)

    if m_court:
        court_city = _norm(m_court.group(1))

        # avoid accidentally capturing too much text
        # Also cut at a spaced dash: the page header runs
        # "Amtsgericht Flensburg - Handelsregister Abteilung B -" together once
        # whitespace is collapsed, and the city must not absorb the rest.
        # A hyphen inside a name ("Baden-Baden") has no surrounding spaces and
        # is left alone.
        court_city = re.split(
            r"\s+[-–—]\s*"
            r"|\s+(?:Abteilung|Abt\.|Wiedergabe|Nummer|Abdruck|des|HRA|HRB)\b",
            court_city,
        )[0].strip()

        out["unternehmen"]["registergericht"] = court_city
        out["unternehmen"]["amtsgericht_verbatim"] = f"Amtsgericht {court_city}"

        _append_hl(out, f"Amtsgerichts {court_city}")
        _append_hl(out, f"Amtsgericht {court_city}")

    # Labelled form first; otherwise the header number, searched only ahead of the
    # partners section so a partner company's HRB cannot win.
    m_hr = re.search(
        rf"Nummer der Firma:\s*({_HR_NUMBER})(?![A-Za-zÄÖÜäöüß])",
        t,
    )

    if not m_hr:
        m_hr = re.search(rf"\b({_HR_NUMBER})(?![A-Za-zÄÖÜäöüß])", head)

    if not m_hr:
        m_hr = re.search(rf"\b({_HR_NUMBER})(?![A-Za-zÄÖÜäöüß])", outside_partners)

    if m_hr:
        hr_number = re.sub(r"\s+", " ", _norm(m_hr.group(1)))
        out["unternehmen"]["handelsregisternummer"] = hr_number
        _append_hl(out, hr_number)

    # ==========================================================================
    # STEP 2 - PDF SECTION  2. a) Firma
    #
    # Reads : the company's own name, e.g.
    #           Windpark Enleni GmbH & Co. KG
    # Writes: unternehmen.name
    #         unternehmen.adresse.nameKomplett
    #         unternehmen.rechtsform   (derived from the name text)
    # ==========================================================================
    m_name = re.search(
        r"2\.\s*a\)\s*Firma:?\s*(.+?)(?=\s*b\)\s*Sitz|\s*b\))",
        t,
        re.S | re.I,
    )

    if m_name:
        company_name = _norm(m_name.group(1))
        company_name = re.sub(r"^(?:EUR|DEM)\s+\d+\.\s*", "", company_name).strip()
        company_name = re.sub(r"^\d+\.\s*", "", company_name).strip()

        out["unternehmen"]["name"] = company_name
        out["unternehmen"]["adresse"]["nameKomplett"] = company_name
        out["unternehmen"]["rechtsform"] = _legal_form_number(company_name)

        _append_hl(out, company_name)

    # ==========================================================================
    # STEP 3 - PDF SECTION  2. b) Sitz, Niederlassung, inlaendische
    #                             Geschaeftsanschrift, Zweigniederlassungen
    #
    # Reads : the seat town, then the street address, e.g.
    #           Behrendorf
    #           Geschaeftsanschrift: Norderdorf 7, 25850 Behrendorf
    # Writes: unternehmen.adresse.ort / .strasse / .hausnummer
    #         unternehmen.adresse.plz / .bundesland
    #
    # Two attempts: the labelled 'Geschaeftsanschrift:' form first, then a
    # looser fallback for documents that omit that exact label.
    # ==========================================================================
    m_sitz = re.search(
        r"b\)\s*Sitz.*?:\s*([A-Za-zÄÖÜäöüß\- ]+)\s*Geschäftsanschrift:",
        t,
        re.S,
    )

    if m_sitz:
        city = _norm(m_sitz.group(1))

        out["unternehmen"]["adresse"]["ort"] = city
        out["unternehmen"]["adresse"]["bundesland"] = _get_bundesland(city)

        _append_hl(out, city)

    # House numbers may be ranges ("31 - 41"), carry a letter ("20 a") or a suffix
    # ("1 / Haus B"); street names may contain digits ("Straße des 17. Juni").
    _HAUSNR = r"\d+\s*[a-zA-Z]?(?:\s*[-–/]\s*\d+\s*[a-zA-Z]?)?(?:\s*/\s*[A-Za-zÄÖÜäöüß ]+)?"

    m_addr = re.search(
        r"Zweigniederlassungen\s+(?P<sitz>[A-Za-zÄÖÜäöüß\- ]+?)\s+"
        rf"(?P<strasse>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-0-9 ]*?[A-Za-zÄÖÜäöüß.])\s+"
        rf"(?P<hausnummer>{_HAUSNR})\s*,\s*"
        rf"(?P<plz>\d{{5}})\s+{_ADDR_ORT}{_ADDR_STOP}",
        t,
    )
    if m_addr:
        street       = _norm(m_addr.group("strasse"))
        house_number = _norm(m_addr.group("hausnummer"))
        post_code    = _norm(m_addr.group("plz"))
        city         = _norm(m_addr.group("ort"))

        out["unternehmen"]["adresse"]["strasse"]    = street
        out["unternehmen"]["adresse"]["hausnummer"] = house_number
        out["unternehmen"]["adresse"]["plz"]        = post_code
        out["unternehmen"]["adresse"]["ort"]        = city
        out["unternehmen"]["adresse"]["bundesland"] = _get_bundesland(city, post_code)

        _append_hl(out, f"{street} {house_number}, {post_code} {city}")

    # Fallback address parser for cases like:
    # Querfurter Weg 1, 06279 Farnstädt
    if not out["unternehmen"]["adresse"]["strasse"]:
        m_addr_fallback = re.search(
            r"(?:Geschäftsanschrift:\s*)?"
            r"(?P<strasse>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-0-9 ]*?[A-Za-zÄÖÜäöüß.])\s+"
            rf"(?P<hausnummer>{_HAUSNR})\s*,\s*"
            rf"(?P<plz>\d{{5}})\s+{_ADDR_ORT}{_ADDR_STOP}",
            t,
        )

        if m_addr_fallback:
            street = _norm(m_addr_fallback.group("strasse"))
            house_number = _norm(m_addr_fallback.group("hausnummer"))
            post_code = _norm(m_addr_fallback.group("plz"))
            city = _norm(m_addr_fallback.group("ort"))

            out["unternehmen"]["adresse"]["strasse"] = street
            out["unternehmen"]["adresse"]["hausnummer"] = house_number
            out["unternehmen"]["adresse"]["plz"] = post_code
            out["unternehmen"]["adresse"]["ort"] = city
            out["unternehmen"]["adresse"]["bundesland"] = _get_bundesland(city, post_code)

            _append_hl(out, f"{street} {house_number}, {post_code} {city}")

    # ==========================================================================
    # STEP 4 - PDF SECTION  5. a) Rechtsform, Beginn und Satzung
    #          PDF SECTION  6. a) Tag der letzten Eintragung
    #
    # Reads : the legal form ('Kommanditgesellschaft'), and the two dates
    # Writes: unternehmen.rechtsform  - ONLY as a fallback, when STEP 2
    #                                   could not derive it from the name
    #
    # The two dates are parsed and then deliberately blanked again, to
    # match what the XML side currently returns. That is a parity choice,
    # not a bug - see the '*****' comment below.
    # ==========================================================================
    if not out["unternehmen"]["rechtsform"]:
        m_form = re.search(
            r"\d\.\s*a\)\s*Rechtsform[^:]*:?\s*(.+?)\s*(?=Beginn:|b\)|c\)|\d\.\s|$)",
            t,
            re.S,
        )
        if m_form:
            out["unternehmen"]["rechtsform"] = _legal_form_number(_norm(m_form.group(1)))

    # ***** Keep these empty to match XML parser output during comparison tests
    out["unternehmen"]["eintragungsdatum"] = ""
    out["unternehmen"]["letzte_aenderung"] = ""

    abruf_dates = re.findall(r"Abruf vom\s+(\d{2}\.\d{2}\.\d{4})", t)

    # if abruf_dates:
    #     _append_hl(out, f"Abruf vom {abruf_dates[-1]}")

    # ==========================================================================
    # STEP 4b - PDF SECTION  4. Prokura
    #
    # Reads : every person listed as holding power of attorney, e.g.
    #           Dr. Kadletz, Andreas, Stuttgart, *09.01.1969
    #           Klatt, Fabian Michael, Stuttgart, *27.04.1986
    # Writes: prokuristen[]
    #
    # Isolates the section once, then scans the WHOLE block, so a section
    # listing several people yields all of them, not just the first.
    # A birth date is required - that is what stops the heading text
    # itself from being read as a person.
    # ==========================================================================
    prokura_match = _PROKURA_SECTION_RE.search(t)

    prokura_block = prokura_match.group("block") if prokura_match else ""

    prokura_person_pattern = re.compile(r"(?:Dr\.\s*)?" + _PERSON_WITH_DOB, re.S)

    for m in prokura_person_pattern.finditer(prokura_block):
        first, last, city, land, dob = _person_from_match(m)
        if not dob:
            continue  # no birth date -> not a person entry

        full = _norm(f"{first} {last}")
        if any(
            p["adresse"]["nameKomplett"] == full
            and p["geburtsdatum"] == dob
            and p["adresse"]["ort"] == city
            for p in out["prokuristen"]
        ):
            continue

        out["prokuristen"].append(
            _person_record(first, last, city, land, dob, _get_bundesland(city), {"share": 0})
        )

        # Optional highlights
        # _append_hl(out, full)
        # _append_hl(out, city)

    # ==========================================================================
    # STEP 5 - PDF SECTION  3. b) Inhaber, persoenlich haftende
    #                             Gesellschafter, ...   (COMPANY entries)
    #
    # Reads : every general partner that is a COMPANY, e.g.
    #           PUTSCH Verwaltungsgesellschaft mbH, Kaiserslautern
    #             (Amtsgericht Kaiserslautern HRB 11792)
    # Writes: persoenlich_haftende_gesellschafter[]
    #
    # A company is recognised by the '(... HR-number)' parenthetical, NOT
    # by seeing 'GmbH' in the name - so an AG or an e.K. is caught too.
    # ==========================================================================
    # phg_block stops at the NEXT section (Prokura/Kommanditist/...), not just
    # at Kommanditisten start: a "4. Prokura:" block commonly sits in between,
    # and since natural-person PHGs are matched anywhere in phg_block (below),
    # an unbounded block would pull the Prokuristen in as well. phg_end was
    # computed above, alongside phg_marker.
    phg_block = t[phg_marker.start(): phg_end] if phg_marker else ""

    for match in _ORG_WITH_REGISTER.finditer(phg_block):
        name = _clean_company_name(match.group("name"))
        if not name:
            continue

        phg_ort, _land = _split_ort_land(match.group("ort"))
        hr_number = re.sub(r"\s+", " ", _norm(match.group("hr")))
        court_city = _court_city(match.group("court") or "")

        if any(
            e["name"] == name and e["handelsregisternummer"] == hr_number
            for e in out["persoenlich_haftende_gesellschafter"]
        ):
            continue

        out["persoenlich_haftende_gesellschafter"].append(
            _org_record(
                name, phg_ort, hr_number, court_city,
                out["unternehmen"]["registergericht"], {"share": 0},
            )
        )

        _append_hl(out, hr_number)
        _append_hl(out, name)
        _append_hl(out, phg_ort)
        if court_city:
            _append_hl(out, f"Amtsgericht {court_city}")

    # ==========================================================================
    # STEP 5b - PDF SECTION  3. b) Inhaber, persoenlich haftende
    #                              Gesellschafter, ...   (PERSON entries)
    #
    # Same PDF section as STEP 5, second pass - this one picks up general
    # partners who are PEOPLE rather than companies, e.g.
    #           Persoenlich haftender Gesellschafter:
    #             Becker, Herta, Koeln, *11.05.1957
    # Writes: natuerliche_phGs[]
    #
    # A birth date is required, which is also what separates a person
    # entry here from a company entry already taken by STEP 5.
    # ==========================================================================
    # Scanning phg_block (not requiring the label immediately before each
    # person) matters: a document that lists several natural-person partners
    # under one label ("Persönlich haftende Gesellschafter: Müller, Hans, ...
    # und Müller, Petra, ...") only has the label once. Anchoring on the label
    # per-entry, as this used to, silently drops every partner after the
    # first. Kommanditisten and Prokuristen already use this block-scan
    # design; this brings PHGs in line with them.
    phg_person_pattern = re.compile(r"(?:Dr\.\s*)?" + _PERSON_WITH_DOB, re.S)

    # Split the block at its role labels, so an "Inhaber:" person lands in
    # company_owner while "Persönlich haftender Gesellschafter:" stays with the
    # partners. Text before the first label keeps the partner default.
    _runs: list[tuple[str, str]] = []
    _labels = list(_HRA_ROLE_LABEL_RE.finditer(phg_block))

    if _labels:
        if _labels[0].start() > 0:
            _runs.append(("", phg_block[: _labels[0].start()]))
        for _i, _lab in enumerate(_labels):
            _stop = _labels[_i + 1].start() if _i + 1 < len(_labels) else len(phg_block)
            _runs.append((_lab.group("role").lower(), phg_block[_lab.end(): _stop]))
    else:
        _runs.append(("", phg_block))

    for _role, _body in _runs:
        _field = HRA_ROLE_TO_PERSON_FIELD.get(
            re.sub(r"\s+", " ", _role).strip(), _HRA_DEFAULT_PERSON_FIELD
        )

        for m in phg_person_pattern.finditer(_body):
            first, last, city, land, dob = _person_from_match(m)
            if not dob:
                continue  # a company entry, handled above

            full = _norm(f"{first} {last}")
            if any(
                p["adresse"]["nameKomplett"] == full
                and p["geburtsdatum"] == dob
                and p["adresse"]["ort"] == city
                for p in out[_field]
            ):
                continue

            out[_field].append(
                _person_record(first, last, city, land, dob, _get_bundesland(city),
                               {"share": 0}, _geburtsname(m))
            )

        # _append_hl(out, full)
        # _append_hl(out, city)

    # ==========================================================================
    # STEP 6 - PDF SECTION  5. c) Kommanditisten, Mitglieder
    #                       (older layout: '4. Kommanditist(en):')
    #
    # Reads : every limited partner, PEOPLE and COMPANIES, each with the
    #         capital contribution printed beside it
    # Writes: kommanditisten_personen[]        (first pass, below)
    #         kommanditisten_gesellschaften[]  (second pass, below)
    # ==========================================================================
    # Printed forms this has to survive:
    # Printed forms this has to survive:
    #   Hellmich, Walter Georg, Luxemburg / Luxemburg, *24.03.1944, Haftsumme: 1.000.000,00 EUR
    #   Andresen, Heike Susann, *19.11.1974, Jübek 4.000,00 EUR
    #   Löffelhardt, Robert Gottlieb, Brühl, *13.04.1964, Einlage: 2.985.000,00 DEM
    # ------------------------------------------------------------

    kp_pattern = re.compile(r"(?:Dr\.\s*)?" + _PERSON_ANY + r"\s*,?\s*" + _MONEY, re.S)

    for m in kp_pattern.finditer(komm_block):
        first, last, city, land, dob = _person_from_match(m)
        full = _norm(f"{first} {last}")
        share = _german_money_to_en(m.group("share"))
        currency = _norm(m.group("currency")).upper()

        if any(
            p["adresse"]["nameKomplett"] == full
            and p["geburtsdatum"] == dob
            and p["adresse"]["ort"] == city
            for p in out["kommanditisten_personen"]
        ):
            continue

        out["kommanditisten_personen"].append(
            _person_record(
                first, last, city, land,
                # Not every Kommanditist has a birth date printed. The XML side
                # reports null for those, so an absent date must be None here,
                # not the empty string, or the two disagree on every such entry.
                dob or None,
                _get_bundesland(city),
                {"share": share, "waehrung": currency},
            )
        )

        _append_hl(out, full)
        _append_hl(out, city)
        _append_hl(out, f"{share} {currency}")

    # ---- STEP 6, second pass: COMPANY Kommanditisten -------------------
    # Same PDF section (5. c), but matching the company shape
    # "Name, Town (Amtsgericht X, HRA nnnn)" plus an optional amount.
    kg_pattern = re.compile(_ORG_WITH_REGISTER.pattern + r"\s*,?\s*(?:" + _MONEY + r")?", re.S)

    for match in kg_pattern.finditer(komm_block):
        name = _clean_company_name(match.group("name"))
        if not name:
            continue

        kg_ort, _land = _split_ort_land(match.group("ort"))
        hr_number = re.sub(r"\s+", " ", _norm(match.group("hr")))
        court_city = _court_city(match.group("court") or "")
        share = _german_money_to_en(match.group("share")) if match.group("share") else 0
        currency = _norm(match.group("currency") or "").upper()

        if any(
            e["name"] == name and e["handelsregisternummer"] == hr_number
            for e in out["kommanditisten_gesellschaften"]
        ):
            continue

        out["kommanditisten_gesellschaften"].append(
            _org_record(
                name, kg_ort, hr_number, court_city,
                out["unternehmen"]["registergericht"],
                {"share": share, "waehrung": currency},
            )
        )

        _append_hl(out, hr_number)
        _append_hl(out, name)
        _append_hl(out, kg_ort)
        if court_city:
            _append_hl(out, f"Amtsgericht {court_city}")
        if share:
            _append_hl(out, f"{share} {currency}")

    return out


# ===========================================================================
# Handelsregister Abteilung B (HRB)
#
# An HRB printout is the same document family but numbers its sections
# differently and holds different roles:
#
#     HRA  3. b) Inhaber, persoenlich haftende Gesellschafter
#     HRB  4. b) Vorstand, Leitungsorgan, geschaeftsfuehrende Direktoren,
#                persoenlich haftende Gesellschafter, Geschaeftsfuehrer, ...
#
#     HRA  5. c) Kommanditisten          HRB  (none - a GmbH has no Kommanditisten)
#     HRA  5. a) Rechtsform              HRB  6. a) Rechtsform
#     HRA  6.    Tag der letzten Eintr.  HRB  7.    Tag der letzten Eintragung
#     HRA  (none)                        HRB  3.    Grund- oder Stammkapital
#
# Inside 4. b) the people are introduced by a role label ("Geschaeftsfuehrer:",
# "Vorstand:"), which is what decides the output list.
# ===========================================================================

# Which output list each printed role maps to.
#
# "Geschaeftsfuehrer -> leitende_personen" is CONFIRMED against a real XML
# output. The others are assumptions: they are grouped here, on one screen, so
# that checking them against your XML and correcting them is a one-line edit
# rather than a hunt through the parser.
HRB_ROLE_TO_FIELD = {
    "geschäftsführer": "leitende_personen",              # confirmed
    "geschäftsführende direktoren": "leitende_personen",  # assumption
    "vorstand": "leitende_personen",                      # assumption; may be "board"
    "liquidator": "leitende_personen",                    # assumption
    "inhaber": "company_owner",                           # assumption
    "persönlich haftender gesellschafter": "persoenlich_haftende_gesellschafter",
    "persönlich haftende gesellschafter": "persoenlich_haftende_gesellschafter",
}

_HRB_DEFAULT_FIELD = "leitende_personen"

# Abteilung A section 3. b) carries a role label of its own. On a sole trader
# (e.K. / Einzelkaufmann) it reads "Inhaber:" and the XML side files that
# person under company_owner, not under the partner lists.
_HRA_ROLE_LABEL_RE = re.compile(
    r"(?P<role>Inhaber(?:in)?"
    r"|Persönlich haftende[rn]?\s+Gesellschafter(?:in)?"
    r")\s*:"
)

HRA_ROLE_TO_PERSON_FIELD = {
    "inhaber": "company_owner",     # confirmed against a real XML output
    "inhaberin": "company_owner",
}

_HRA_DEFAULT_PERSON_FIELD = "natuerliche_phGs"

# A surname must begin with a capital (after any lower-case particle). The HRA
# patterns are looser, which is safe there because a birth date is required;
# here entries may carry no date, so the stricter head keeps the boilerplate
# prose in this section from being read as a person.
_NAME_HEAD_STRICT = (
    rf"(?P<nachname>{_SURNAME_PARTICLE}[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-']*)\s*,\s*"
    r"(?P<vorname>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-'. ]*?)\s*,\s*"
)

# Where the organ section starts and stops.
_HRB_ORGAN_MARKER_RE = re.compile(
    r"(?:\d+\s*\.\s*)?b\)\s*Vorstand\b"
    r"|Geschäftsführer\s*:"
    r"|Vorstand\s*:"
    r"|Geschäftsführende\s+Direktoren\s*:"
)

_HRB_ORGAN_END_RE = re.compile(
    r"\d+\s*\.\s*Prokura\b"
    r"|(?:\d+\s*\.\s*)?[a-z]?\)?\s*Rechtsform\s*,\s*Beginn"
    r"|Tag\s+der\s+letzten\s+Eintragung"
    r"|Abruf\s+vom"
)

# A role label introducing one or more people inside the organ section.
_HRB_ROLE_LABEL_RE = re.compile(
    r"(?P<role>"
    r"Geschäftsführer(?:in)?"
    r"|Geschäftsführende\s+Direktoren"
    r"|Vorstand"
    r"|Liquidator(?:in)?"
    r"|Inhaber(?:in)?"
    r"|Persönlich haftende[rn]?\s+Gesellschafter(?:in)?"
    r")\s*:"
)

# A person as printed in an HRB organ section. The birth date may be absent —
# an older entry prints a profession instead ("Asmussen, Hans P., Handewitt,
# Landwirt") — so all three orderings are spelled out.
_HRB_PERSON_RE = re.compile(
    _NAME_HEAD_STRICT +
    r"(?:"
    rf"\*(?P<dob1>{_DATE})\s*,?\s*(?P<ort1>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-./]*(?:\s+[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-./]*){{0,2}}?)"
    r"|"
    rf"(?P<ort2>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-./]*(?:\s+[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-./]*){{0,2}}?)\s*,?\s*\*(?P<dob2>{_DATE})"
    r"|"
    r"(?P<ort3>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-./]*(?:\s+[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-./]*){0,2}?)"
    r")"
    # stop at a profession, the next entry, a new label, or the end
    r"(?=\s*,\s*[A-ZÄÖÜ]|\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-']*\s*,|\s*$)",
    re.S,
)


def detect_register_type(text: str) -> str:
    """'B' for a Handelsregister Abteilung B printout, otherwise 'A'.

    Reads the printed department line and the register number, not the file
    name, so a mislabelled file still routes correctly.
    """
    t = normalise_for_parsing(text)

    if re.search(r"Handelsregister\s+Abteilung\s+B\b", t):
        return "B"
    if re.search(r"Handelsregister\s+Abteilung\s+A\b", t):
        return "A"

    # No department line: fall back to which register number appears first.
    m = re.search(r"\bHR(?P<kind>[AB])\s*\d", t)
    if m:
        return m.group("kind")

    # An unmistakably HRB-only section.
    if re.search(r"Grund-\s*oder\s*Stammkapital", t):
        return "B"

    return "A"


def parse_handelsregister_b_text(text: str) -> dict:
    """Parse a Handelsregister Abteilung B printout into the same schema.

    The company-level fields (name, register number, court, address, legal
    form) are printed identically in both departments, so those come from the
    Abteilung A pass; the HRA-only lists simply stay empty on a B document.
    Only the organ section differs, and that is what this adds.
    """
    out = parse_handelsregister_a_text(text)
    t = normalise_for_parsing(text)

    marker = _HRB_ORGAN_MARKER_RE.search(t)
    if not marker:
        return out

    end = len(t)
    boundary = _HRB_ORGAN_END_RE.search(t, marker.end())
    if boundary:
        end = boundary.start()

    block = t[marker.start():end]

    # Split the block into (role, text) runs so each person lands in the list
    # its own label dictates.
    runs: list[tuple[str, str]] = []
    labels = list(_HRB_ROLE_LABEL_RE.finditer(block))

    if labels:
        for i, lab in enumerate(labels):
            stop = labels[i + 1].start() if i + 1 < len(labels) else len(block)
            runs.append((lab.group("role").lower(), block[lab.end():stop]))
    else:
        runs.append(("", block))

    for role, body in runs:
        field = HRB_ROLE_TO_FIELD.get(re.sub(r"\s+", " ", role).strip(), _HRB_DEFAULT_FIELD)

        # A company acting as an organ (a KGaA's general partner, say) is
        # recognised by its register parenthetical, exactly as in Abteilung A.
        for m in _ORG_WITH_REGISTER.finditer(body):
            name = _clean_company_name(m.group("name"))
            if not name:
                continue
            org_ort, _land = _split_ort_land(m.group("ort"))
            hr_number = re.sub(r"\s+", " ", _norm(m.group("hr")))
            target = field if field in ("persoenlich_haftende_gesellschafter",
                                        "company_owner") else "persoenlich_haftende_gesellschafter"
            if any(e["name"] == name and e["handelsregisternummer"] == hr_number
                   for e in out[target]):
                continue
            out[target].append(
                _org_record(name, org_ort, hr_number, _court_city(m.group("court") or ""),
                            out["unternehmen"]["registergericht"], {"share": 0})
            )
            _append_hl(out, name)
            _append_hl(out, hr_number)

        for m in _HRB_PERSON_RE.finditer(body):
            groups = m.groupdict()
            last = re.sub(r"^Dr\.\s*", "", _norm(m.group("nachname"))).strip()
            first = _norm(m.group("vorname"))
            raw_ort = groups.get("ort1") or groups.get("ort2") or groups.get("ort3") or ""
            city, land = _split_ort_land(raw_ort)
            dob_raw = groups.get("dob1") or groups.get("dob2")
            dob = _to_iso_date(dob_raw) if dob_raw else None

            full = _norm(f"{first} {last}")
            if any(p["adresse"]["nameKomplett"] == full
                   and p["geburtsdatum"] == dob
                   and p["adresse"]["ort"] == city
                   for p in out[field]):
                continue

            out[field].append(
                _person_record(first, last, city, land, dob,
                               _get_bundesland(city), {"share": 0})
            )
            _append_hl(out, full)

    return out


def parse_handelsregister_text(text: str) -> dict:
    """Parse either department, routing on what the document says it is."""
    if detect_register_type(text) == "B":
        return parse_handelsregister_b_text(text)
    return parse_handelsregister_a_text(text)


def parse_handelsregister_or_none(text: str) -> dict | None:
    """Auto-routing counterpart of parse_handelsregister_a_or_none."""
    out = parse_handelsregister_text(text)
    company = out.get("unternehmen", {})

    missing = [f for f in ("name", "handelsregisternummer", "registergericht")
               if not company.get(f)]
    if missing:
        print("[DEBUG] HR parser missing fields:", missing)
        print("[DEBUG] Parsed company so far:", company)
        return None
    return out


def parse_handelsregister_a_or_none(text: str) -> dict | None:
    """
    Return parsed output only if the minimum required company fields were found.
    Otherwise return None.
    """
    out = parse_handelsregister_a_text(text)

    company = out.get("unternehmen", {})
    missing = []
    if not company.get("name"):
        missing.append("name")

    if not company.get("handelsregisternummer"):
        missing.append("handelsregisternummer")

    if not company.get("registergericht"):
        missing.append("registergericht")

    if missing:
        print("[DEBUG] HRA parser missing fields:", missing)
        print("[DEBUG] Parsed company so far:", company)

        return None
    return out
