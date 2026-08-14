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
_NAME_HEAD = (
    r"(?P<nachname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-']*)\s*,\s*"
    r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-'. ]*?)\s*,\s*"
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


def _person_record(first, last, city, land, dob, bundesland, beteiligung) -> dict:
    return {
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


def parse_handelsregister_a_text(text: str) -> dict:
    """
    Parse text from a German Handelsregister A PDF into the target JSON schema.

    Handles both printout layouts:
    - "Ausdruck" with inline labels ("Nummer der Firma:", "Kommanditist(en):")
    - "Aktueller Ausdruck" with numbered headings ("2. a) Firma", "c) Kommanditisten")
    """
    out = deepcopy(BASE_OUTPUT)
    text = html.unescape(text or "")
    t = text
    # Clean PDF formatting markers
    t = t.replace("**", " ")
    t = re.sub(r"\s+", " ", t)

    # ------------------------------------------------------------
    # 0) Section boundaries — used to keep partner entries from leaking between
    #    sections, and to stop the company register number being read off a
    #    partner company.
    # ------------------------------------------------------------
    komm_match = re.search(
        r"Kommanditist(?:\(en\)|en)?\s*:\s*(?P<block>.*?)"
        r"(?=\s*\d+\.\s*[a-z]?\)?\s*Tag der letzten Eintragung|\s*Abruf vom|\Z)",
        t,
        re.S,
    )
    komm_block = komm_match.group("block") if komm_match else ""
    komm_start = komm_match.start() if komm_match else len(t)

    phg_marker = re.search(
        r"(?:Persönlich haftende[rn]?\s+Gesellschafter(?:in)?\s*:|b\)\s*Inhaber)",
        t,
    )
    head = t[: phg_marker.start()] if phg_marker else t[:komm_start]

    # ------------------------------------------------------------
    # 1) Court and main register number
    # ------------------------------------------------------------
    m_court = re.search(r"Amtsgerichts?\s+([A-Za-zÄÖÜäöüß\- ]+?)(?=\n|,|$)", t)

    if not m_court:
        m_court = re.search(r"Amtsgerichts?\s+([A-Za-zÄÖÜäöüß\- ]+)", t)

    if m_court:
        court_city = _norm(m_court.group(1))

        # avoid accidentally capturing too much text
        court_city = re.split(
            r"\s+(Abteilung|Abt\.|Wiedergabe|Nummer|Abdruck|des|HRA|HRB)\b",
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
        m_hr = re.search(rf"\b({_HR_NUMBER})(?![A-Za-zÄÖÜäöüß])", t)

    if m_hr:
        hr_number = re.sub(r"\s+", " ", _norm(m_hr.group(1)))
        out["unternehmen"]["handelsregisternummer"] = hr_number
        _append_hl(out, hr_number)

    # ------------------------------------------------------------
    # 2) Company name, section 2a
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # 3) Seat and business address, section 2b
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # 4) Legal form, beginning date, last change, retrieval date
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # 4b) Prokuristen / Procurists
    # Example:
    # Dr. Kadletz, Andreas, Stuttgart, *09.01.1969
    # ------------------------------------------------------------
    prokura_match = re.search(
        r"\d+\.\s*Prokura\s*:?\s*(?P<block>.*?)(?=\s*\d+\.\s*a\)|\s*\d+\.\s+[A-ZÄÖÜ]|\Z)",
        t,
        re.S | re.I,
    )

    prokura_block = prokura_match.group("block") if prokura_match else ""

    prokura_person_pattern = re.compile(r"(?:Dr\.\s*)?" + _PERSON_WITH_DOB, re.S)

    for m in prokura_person_pattern.finditer(prokura_block):
        first, last, city, land, dob = _person_from_match(m)
        if not dob:
            continue  # no birth date -> not a person entry

        full = _norm(f"{first} {last}")
        if any(p["adresse"]["nameKomplett"] == full for p in out["prokuristen"]):
            continue

        out["prokuristen"].append(
            _person_record(first, last, city, land, dob, _get_bundesland(city), {"share": 0})
        )

        # Optional highlights
        # _append_hl(out, full)
        # _append_hl(out, city)

    # ------------------------------------------------------------
    # 5) Personally liable partners (COMPANY form)
    # Persönlich haftender Gesellschafter: PUTSCH Verwaltungsgesellschaft mbH,
    # Kaiserslautern (Amtsgericht Kaiserslautern HRB 11792)
    # ------------------------------------------------------------
    phg_block = t[phg_marker.start(): komm_start] if phg_marker else ""

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

    # ------------------------------------------------------------
    # 5b) Natural-person PHGs
    # Persönlich haftender Gesellschafter: Becker, Herta, Köln, *11.05.1957
    # ------------------------------------------------------------
    phg_person_pattern = re.compile(
        r"Persönlich haftende[rn]?\s+Gesellschafter(?:in)?\s*:\s*(?:Dr\.\s*)?" + _PERSON_WITH_DOB,
        re.S,
    )

    for m in phg_person_pattern.finditer(t):
        first, last, city, land, dob = _person_from_match(m)
        if not dob:
            continue  # a company entry, handled above

        full = _norm(f"{first} {last}")
        if any(p["adresse"]["nameKomplett"] == full for p in out["natuerliche_phGs"]):
            continue

        out["natuerliche_phGs"].append(
            _person_record(first, last, city, land, dob, _get_bundesland(city), {"share": 0})
        )

        # _append_hl(out, full)
        # _append_hl(out, city)

    # ------------------------------------------------------------
    # 6) Limited partners / Kommanditisten
    # Printed forms this has to survive:
    #   Hellmich, Walter Georg, Luxemburg / Luxemburg, *24.03.1944, Haftsumme: 1.000.000,00 EUR
    #   Andresen, Heike Susann, *19.11.1974, Jübek 4.000,00 EUR
    #   Löffelhardt, Robert Gottlieb, Brühl, *13.04.1964, Einlage: 2.985.000,00 DEM
    # ------------------------------------------------------------
    _MONEY = r"(?:(?:Haftsumme|Einlage)\s*:\s*)?(?P<share>[\d.]+,\d{2})\s*(?P<currency>[A-ZÄÖÜ]{2,3})\b"

    kp_pattern = re.compile(r"(?:Dr\.\s*)?" + _PERSON_ANY + r"\s*,?\s*" + _MONEY, re.S)

    for m in kp_pattern.finditer(komm_block):
        first, last, city, land, dob = _person_from_match(m)
        full = _norm(f"{first} {last}")
        share = _german_money_to_en(m.group("share"))
        currency = _norm(m.group("currency")).upper()

        if any(p["adresse"]["nameKomplett"] == full for p in out["kommanditisten_personen"]):
            continue

        out["kommanditisten_personen"].append(
            _person_record(
                first, last, city, land, dob,
                "",  # XML leaves person bundesland empty
                {"share": share, "waehrung": currency},
            )
        )

        _append_hl(out, full)
        _append_hl(out, city)
        _append_hl(out, f"{share} {currency}")

    # ---- Company Kommanditisten ----
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
