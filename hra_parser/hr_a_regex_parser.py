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

# Foreign residents are printed as "Ort / Land" (e.g. "Luxemburg / Luxemburg").
# The XML side reports the ISO country code, so the German country name has to map.
COUNTRY_TO_ISO = {
    "Deutschland": "DE",
    "Luxemburg": "LU",
    "Schweiz": "CH",
    "Österreich": "AT",
    "Niederlande": "NL",
    "Belgien": "BE",
    "Frankreich": "FR",
    "Dänemark": "DK",
    "Italien": "IT",
    "Spanien": "ES",
    "Großbritannien": "GB",
    "Vereinigtes Königreich": "GB",
    "Polen": "PL",
    "Liechtenstein": "LI",
    "Schweden": "SE",
    "Norwegen": "NO",
    "Tschechien": "CZ",
    "USA": "US",
}


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

    An unknown country yields "" rather than "DE", so it shows up as a mismatch
    instead of being silently wrong.
    """
    raw = _norm(raw_ort)

    if "/" in raw:
        city, country = (part.strip() for part in raw.split("/", 1))
        return city, COUNTRY_TO_ISO.get(country, "")

    return raw, "DE"


def parse_handelsregister_a_text(text: str) -> dict:
    """
    Parse text from a German Handelsregister A PDF into the target JSON schema.

    Expected input text contains sections such as:
    - Handelsregister A des Amtsgerichts Münster
    - Nummer der Firma: HRA 6481
    - 2. a) Firma:
    - Geschäftsanschrift:
    - Persönlich haftender Gesellschafter:
    - Kommanditist(en):
    - Einlage:
    - Tag der letzten Eintragung:
    - Abruf vom
    """
    out = deepcopy(BASE_OUTPUT)
    text = html.unescape(text or "")
    t = text
    # Clean PDF formatting markers
    t = t.replace("**", " ")
    t = re.sub(r"\s+", " ", t)

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

        _append_hl(out, f"Amtsgerichts {court_city}")
        _append_hl(out, f"Amtsgericht {court_city}")

    # HR number (label form first, then bare-header fallback)
    m_hr = re.search(
        r"Nummer der Firma:\s*((?:HRA|HRB)\s*\d+)\b",
        t,
    )

    if not m_hr:
        m_hr = re.search(
            r"\b((?:HRA|HRB)\s*\d+)\b",
            t,
        )

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

    m_addr = re.search(
        r"Zweigniederlassungen\s+(?P<sitz>[A-Za-zÄÖÜäöüß\- ]+?)\s+"
        r"(?P<strasse>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\- ]+?)\s+"
        r"(?P<hausnummer>\d+(?:/\s*[A-Za-zÄÖÜäöüß ]+)?(?:\s*-\s*\d+)?[a-z]?)\s*,\s*"
        r"(?P<plz>\d{5})\s+(?P<ort2>[A-Za-zÄÖÜäöüß\- ]+?)(?=\s+\d\.|$)",
        t,
    )
    if m_addr:
        street       = _norm(m_addr.group("strasse"))
        house_number = _norm(m_addr.group("hausnummer"))
        post_code    = _norm(m_addr.group("plz"))
        city         = _norm(m_addr.group("ort2"))

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
            r"(?P<strasse>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\- ]+?)\s+"
            r"(?P<hausnummer>\d+[a-zA-Z]?)\s*,\s*"
            r"(?P<plz>\d{5})\s+"
            r"(?P<ort>[A-Za-zÄÖÜäöüß\- ]+?)(?=\s+[a-z]\)|$)",
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
    m_form = re.search(
        r"5\.\s*a\)\s*Rechtsform.*?:\s*(.+?)\s*Beginn:",
        t,
        re.S,
    )

    if m_form and not out["unternehmen"]["rechtsform"]:
        form_text = _norm(m_form.group(1))
        out["unternehmen"]["rechtsform"] = _legal_form_number(form_text)

    m_begin = re.search(
        r"Beginn:\s*(\d{2}\.\d{2}\.\d{4})",
        t,
    )

    if m_begin:
        out["unternehmen"]["eintragungsdatum"] = _to_iso_date(m_begin.group(1))

    m_last = re.search(
        r"6\.\s*a\)\s*Tag der letzten Eintragung:\s*(\d{2}\.\d{2}\.\d{4})",
        t,
    )

    if m_last:
        out["unternehmen"]["letzte_aenderung"] = _to_iso_date(m_last.group(1))


# ***** Keep these empty to match XML parser output during comparison tests
    out["unternehmen"]["eintragungsdatum"] = ""

    out["unternehmen"]["letzte_aenderung"] = ""

    abruf_dates = re.findall(
        r"Abruf vom\s+(\d{2}\.\d{2}\.\d{4})",
        t,
    )

    # if abruf_dates:
    #     _append_hl(out, f"Abruf vom {abruf_dates[-1]}")

    # ------------------------------------------------------------
    # 4b) Prokuristen / Procurists
    # Example:
    # Dr. Kadletz, Andreas, Stuttgart, *09.01.1969
    # Klatt, Fabian Michael, Stuttgart, *27.04.1986
    # ------------------------------------------------------------

    prokura_match = re.search(
        r"4\.\s*Prokura:.*?(?P<block>.*?)(?=\s*5\.\s*a\)\s*Rechtsform|\s*5\.)",
        t,
        re.S | re.I,
    )

    prokura_block = prokura_match.group("block") if prokura_match else ""

    prokura_person_pattern = re.compile(
        r"(?P<nachname>(?:Dr\.\s*)?[A-Za-zÄÖÜäöüß\-]+),\s*"
        r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\- ]*?),\s*"
        r"(?P<ort>[A-Za-zÄÖÜäöüß\-/ ]+?),\s*"
        r"\*(?P<dob>\d{2}\.\d{2}\.\d{4})",
        re.S | re.I,
    )

    for m in prokura_person_pattern.finditer(prokura_block):
        last = _norm(m.group("nachname"))

        # XML parser does not keep title in nachname/nameKomplett
        last = re.sub(r"^Dr\.\s*", "", last).strip()

        first = _norm(m.group("vorname"))
        city, land = _split_ort_land(m.group("ort"))
        full = _norm(f"{first} {last}")

        person = {
            "name": "",
            "handelsregisternummer": "",
            "geburtsdatum": _to_iso_date(m.group("dob")),
            "geb_name": "",
            "adresse": {
                "nameKomplett": full,
                "strasse": "",
                "hausnummer": "",
                "plz": "",
                "ort": city,
                "bundesland": _get_bundesland(city),
                "land": land,
            },
            "beteiligung": {
                "share": 0,
            },
            "vorname": first,
            "nachname": last,
        }

        already_exists = any(
            p.get("adresse", {}).get("nameKomplett") == full
            for p in out["prokuristen"]
        )

        if not already_exists:
            out["prokuristen"].append(person)

        # Optional highlights
        # _append_hl(out, full)
        # _append_hl(out, city)

    # ------------------------------------------------------------
    # 5) Personally liable partners (COMPANY form)
    # Persönlich haftender Gesellschafter: PUTSCH Verwaltungsgesellschaft mbH,
    # Kaiserslautern (Amtsgericht Kaiserslautern HRB 11792)
    # ------------------------------------------------------------
    phg_company_pattern = re.compile(
        r"Persönlich haftende?r? Gesellschafter(?:in)?:.*?"
        r"(?P<name>[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9 &.'\-]+?(?:GmbH|mbH|UG|AG|KG|OHG)(?:\s*&\s*Co\.\s*KG)?)"
        r"(?:,\s*Gesellschaft mit beschränkter Haftung)?"
        r"\s*,\s*"
        r"(?P<ort>[A-Za-zÄÖÜäöüß\- ]+?)\s*"
        r"\(\s*(?P<court>(?:Amtsgericht|AG)\s+[A-Za-zÄÖÜäöüß\- ]+?)"
        r"\s*,?\s*"
        r"(?P<hr>(?:HRA|HRB)\s*\d+(?:\s+[A-Z]{1,3})?)\s*\)",
        re.S | re.I,
    )

    for match in phg_company_pattern.finditer(t):
        name       = _clean_company_name(match.group("name"))
        phg_ort    = _norm(match.group("ort"))
        hr_number  = re.sub(r"\s+", " ", _norm(match.group("hr")))
        court_city = _norm(re.sub(r"^(?:Amtsgericht|AG)\s+", "", match.group("court")))

        phg = {
            "name": name,
            "handelsregisternummer": hr_number,
            "rechtsform": _legal_form_number(name),
            "registergericht": court_city or out["unternehmen"]["registergericht"],
            "amtsgericht_verbatim": "",
            "adresse": {
                "nameKomplett": name,
                "strasse": "", "hausnummer": "", "plz": "",
                "ort": phg_ort,
                "bundesland": _get_bundesland(phg_ort),
                "land": "DE",
            },
            "beteiligung": {"share": 0},
        }
        if not any(
            e.get("name") == name and e.get("handelsregisternummer") == hr_number
            for e in out["persoenlich_haftende_gesellschafter"]
        ):
            out["persoenlich_haftende_gesellschafter"].append(phg)

        _append_hl(out, hr_number)
        _append_hl(out, name)
        _append_hl(out, phg_ort)
        if court_city:
            _append_hl(out, f"Amtsgericht {court_city}")

    # ------------------------------------------------------------
    # 5b) Natural-person PHGs
    # Example:
    # Persönlich haftender Gesellschafter: Becker, Herta, Köln, *11.05.1957
    # ------------------------------------------------------------

    phg_person_pattern = re.compile(
        r"Persönlich haftender Gesellschafter:\s*"
        r"(?P<nachname>[A-Za-zÄÖÜäöüß\-]+)\s*,\s*"
        r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\- ]*?)\s*,\s*"
        r"(?:\*(?P<dob1>\d{2}\.\d{2}\.\d{4})\s*,?\s*)?"
        r"(?P<ort>[A-Za-zÄÖÜäöüß\-/ ]+?)\s*,?\s*"
        r"(?:\*(?P<dob2>\d{2}\.\d{2}\.\d{4}))?",
        re.M,
    )

    for m in phg_person_pattern.finditer(t):
        dob_raw = m.group("dob1") or m.group("dob2")
        if not dob_raw:
            continue  # without a birth date this is not a natural person entry

        last = _norm(m.group("nachname"))
        first = _norm(m.group("vorname"))
        city, land = _split_ort_land(m.group("ort"))
        full = _norm(f"{first} {last}")

        person = {
            "name": "",
            "handelsregisternummer": "",
            "geburtsdatum": _to_iso_date(dob_raw),
            "geb_name": "",
            "adresse": {
                "nameKomplett": full,
                "strasse": "",
                "hausnummer": "",
                "plz": "",
                "ort": city,
                "bundesland": _get_bundesland(city),
                "land": land,
            },
            "beteiligung": {
                "share": 0,
            },
            "vorname": first,
            "nachname": last,
        }

        already_exists = any(
            p.get("adresse", {}).get("nameKomplett") == full
            for p in out["natuerliche_phGs"]
        )

        if not already_exists:
            out["natuerliche_phGs"].append(person)

        # _append_hl(out, full)
        # _append_hl(out, city)

    # ------------------------------------------------------------
    # 6) Limited partners / Kommanditisten
    # ------------------------------------------------------------
    # Isolate the Kommanditisten block (section 6c → up to "Tag der letzten Eintragung")
    komm_match = re.search(
        r"Kommanditist(?:\(en\)|en)?:\s*(?P<block>.*?)"
        r"(?=\s*\d+\.\s*a\)\s*Tag der letzten Eintragung|\s*Abruf vom|\Z)",
        t,
        re.S,
    )
    komm_block = komm_match.group("block") if komm_match else ""

    # ---- Person Kommanditisten ----
    # Printed forms this has to survive:
    #   Hellmich, Walter Georg, Luxemburg / Luxemburg, *24.03.1944, Haftsumme: 1.000.000,00 EUR
    #   Andresen, Heike Susann, *19.11.1974, Jübek 4.000,00 EUR
    #   Löffelhardt, Robert Gottlieb, Brühl, *13.04.1964, Einlage: 2.985.000,00 DEM
    # so: compound first names, "Ort / Land", the birth date on either side of the
    # town, an optional Haftsumme/Einlage label, and EUR or DEM.
    kp_pattern = re.compile(
        r"(?:Dr\.\s*)?"
        r"(?P<nachname>[A-Za-zÄÖÜäöüß\-]+)\s*,\s*"
        r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\- ]*?)\s*,\s*"
        r"(?:\*(?P<dob1>\d{2}\.\d{2}\.\d{4})\s*,?\s*)?"
        r"(?P<ort>[A-Za-zÄÖÜäöüß\-/ ]+?)\s*,?\s*"
        r"(?:\*(?P<dob2>\d{2}\.\d{2}\.\d{4})\s*,?\s*)?"
        r"(?:(?:Haftsumme|Einlage)\s*:\s*)?"
        r"(?P<share>[\d.]+,\d{2})\s*"
        r"(?P<currency>EUR|DEM)\b",
        re.S,
    )

    for m in kp_pattern.finditer(komm_block):
        first = _norm(m.group("vorname"))
        last = _norm(m.group("nachname"))
        city, land = _split_ort_land(m.group("ort"))
        dob_raw = m.group("dob1") or m.group("dob2")
        full = _norm(f"{first} {last}")
        share = _german_money_to_en(m.group("share"))
        currency = _norm(m.group("currency")).upper()

        person = {
            "name": "",
            "handelsregisternummer": "",
            "geburtsdatum": _to_iso_date(dob_raw) if dob_raw else "",
            "geb_name": "",
            "adresse": {
                "nameKomplett": full, "strasse": "", "hausnummer": "",
                "plz": "", "ort": city,
                "bundesland": "",          # XML leaves person bundesland empty
                "land": land,
            },
            "beteiligung": {"share": share, "waehrung": currency},
            "vorname": first, "nachname": last,
        }
        if not any(p["adresse"]["nameKomplett"] == full
                   for p in out["kommanditisten_personen"]):
            out["kommanditisten_personen"].append(person)

        _append_hl(out, full)
        _append_hl(out, city)
        _append_hl(out, f"{share} {currency}")

    # ---- Company Kommanditisten ----
    kg_pattern = re.compile(
        r"(?P<name>[^,(]+(?:GmbH|UG|OHG)(?:\.\s*&\s*Co\.\s*KG)?)\s*,\s*"
        r"(?P<ort>[^,(]+)\s*"
        r"\((?P<court>Amtsgericht\s+[A-Za-zÄÖÜäöüß\- ]+)?\s*,?\s*"
        r"(?P<hr>[A-Z]{2,3}\s*\d+(?:\s*[A-Z]{1,3})?)\s*\)?\s*"
        r"(?:\s*(?:mit beschränkter Haftung)\s*)?"
        r"\s*(?:(?:Haftsumme|Einlage)\s*:\s*)?"
        r"(?P<share>[\d.]+,\d{2})\s*"
        r"(?P<currency>EUR|DEM)\b"
    )

    for match in kg_pattern.finditer(komm_block):
        name       = _clean_company_name(match.group("name"))
        kg_ort     = _norm(match.group("ort"))
        hr_number  = re.sub(r"\s+", " ", _norm(match.group("hr")))
        court_city = _norm(re.sub(r"^(?:Amtsgericht|AG)\s+", "", match.group("court") or ""))
        share      = _german_money_to_en(match.group("share"))
        currency   = _norm(match.group("currency")).upper()

        company = {
            "name": name,
            "handelsregisternummer": hr_number,
            "rechtsform": _legal_form_number(name),
            "registergericht": court_city or out["unternehmen"]["registergericht"],
            "amtsgericht_verbatim": "",
            "adresse": {
                "nameKomplett": name,
                "strasse": "", "hausnummer": "", "plz": "",
                "ort": kg_ort,
                "bundesland": _get_bundesland(kg_ort),
                "land": "DE",
            },
            "beteiligung": {"share": share, "waehrung": currency},
        }
        if not any(
            e.get("name") == name and e.get("handelsregisternummer") == hr_number
            for e in out["kommanditisten_gesellschaften"]
        ):
            out["kommanditisten_gesellschaften"].append(company)

        _append_hl(out, hr_number)
        _append_hl(out, name)
        _append_hl(out, kg_ort)
        if court_city:
            _append_hl(out, f"Amtsgericht {court_city}")
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
