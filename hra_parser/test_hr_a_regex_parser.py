"""Self-contained regression suite for hr_a_regex_parser.py.

Run it any time you touch the parser:

    python3 test_hr_a_regex_parser.py

Each check is a small, realistic document fragment plus assertions about what
must come out of it. A PASS/FAIL line prints per check; the process exits
non-zero if anything fails, so this can also gate a commit or CI step.

Inside the real kyc_assistant project this imports the real
kyc_assistant.domain.legal_forms and kyc_assistant.services.xml_parser.
Standalone (as here, outside that project), it falls back to small stand-ins
so the suite still runs and still proves the parsing logic itself is correct
— only the legal-form-code lookup and the Bundesland lookup are stood in for,
neither of which affects what gets extracted, only how a couple of fields get
labelled downstream.
"""

import sys
import types

# ---------------------------------------------------------------------------
# Fall back to stand-ins only if the real kyc_assistant modules are not
# importable — e.g. when running this file outside the main project.
# ---------------------------------------------------------------------------
try:
    import kyc_assistant.domain.legal_forms  # noqa: F401
except ImportError:
    _lf = types.ModuleType("kyc_assistant.domain.legal_forms")

    def _extract_rechtsform(text):
        t = (text or "").lower()
        if "gmbh & co. kg" in t:
            return "gmbh_co_kg"
        if "mbh" in t or "gmbh" in t:
            return "gmbh"
        if "kommanditgesellschaft" in t:
            return "kg"
        return ""

    _lf.extract_rechtsform = _extract_rechtsform
    _lf.rechtsform_map = {"gmbh_co_kg": "61", "gmbh": "60", "kg": "59"}

    class _Bundesland:
        plz_bundesland_mapping = {}

        @staticmethod
        def get_bundesland_by_ort(_ort):
            return ""

    _xp = types.ModuleType("kyc_assistant.services.xml_parser")
    _xp.get_bundesland_data = lambda: _Bundesland()

    for _name, _mod in [
        ("kyc_assistant", types.ModuleType("kyc_assistant")),
        ("kyc_assistant.domain", types.ModuleType("kyc_assistant.domain")),
        ("kyc_assistant.domain.legal_forms", _lf),
        ("kyc_assistant.services", types.ModuleType("kyc_assistant.services")),
        ("kyc_assistant.services.xml_parser", _xp),
    ]:
        sys.modules.setdefault(_name, _mod)

import hr_a_regex_parser as P  # noqa: E402

checks = []


def check(name):
    def register(fn):
        checks.append((name, fn))
        return fn
    return register


# ---------------------------------------------------------------------------
@check("company name + register number + address (baseline)")
def _():
    doc = """
    Nummer der Firma: HRA 100
    2. a) Firma: Beispiel GmbH & Co. KG
    b) Sitz: Berlin Geschäftsanschrift: Musterstraße 5, 10115 Berlin
    5. a) Rechtsform: Kommanditgesellschaft
    6. a) Tag der letzten Eintragung: 01.01.2020
    """
    r = P.parse_handelsregister_a_text(doc)
    u = r["unternehmen"]
    assert u["name"] == "Beispiel GmbH & Co. KG", u["name"]
    assert u["handelsregisternummer"] == "HRA 100", u["handelsregisternummer"]
    assert u["adresse"]["strasse"] == "Musterstraße", u["adresse"]
    assert u["adresse"]["hausnummer"] == "5"
    assert u["adresse"]["plz"] == "10115"
    assert u["adresse"]["ort"] == "Berlin"


@check("two company general partners, no repeated label needed")
def _():
    doc = """
    Nummer der Firma: HRA 200
    2. a) Firma: Doppelt KG
    b) Sitz: Hamburg Geschäftsanschrift: Hafenweg 1, 20095 Hamburg
    b) Inhaber, persönlich haftende Gesellschafter:
    Alpha Verwaltungs GmbH, Hamburg (Amtsgericht Hamburg, HRB 111)
    Beta Verwaltungs GmbH, Hamburg (Amtsgericht Hamburg, HRB 222)
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Meier, Anna, Hamburg, *01.01.1980, Haftsumme: 1.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2020
    """
    r = P.parse_handelsregister_a_text(doc)
    names = {p["name"] for p in r["persoenlich_haftende_gesellschafter"]}
    assert names == {"Alpha Verwaltungs GmbH", "Beta Verwaltungs GmbH"}, names


@check("two natural-person general partners under ONE label")
def _():
    doc = """
    Nummer der Firma: HRA 300
    2. a) Firma: Zwei Geschwister GbR
    b) Sitz: Köln Geschäftsanschrift: Ringstraße 1, 50667 Köln
    b) Inhaber, persönlich haftende Gesellschafter:
    Persönlich haftende Gesellschafter: Müller, Hans, Köln, *01.01.1970 und Müller, Petra, Köln, *02.02.1972
    5. a) Rechtsform: Kommanditgesellschaft
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    names = {p["adresse"]["nameKomplett"] for p in r["natuerliche_phGs"]}
    assert names == {"Hans Müller", "Petra Müller"}, names


@check("older layout: label repeated per natural-person partner")
def _():
    doc = """
    Handelsregister A des Amtsgerichts Köln
    Nummer der Firma: HRA 301
    2. a) Firma: Alt KG
    b) Sitz: Köln Geschäftsanschrift: Domstraße 1, 50667 Köln
    Persönlich haftender Gesellschafter: Schulz, Otto, Köln, *03.03.1950
    Persönlich haftender Gesellschafter: Schulz, Erna, Köln, *04.04.1955
    5. a) Rechtsform: Kommanditgesellschaft
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    names = {p["adresse"]["nameKomplett"] for p in r["natuerliche_phGs"]}
    assert names == {"Otto Schulz", "Erna Schulz"}, names


@check("Prokura section does not leak into natuerliche_phGs")
def _():
    doc = """
    Nummer der Firma: HRA 400
    2. a) Firma: Prokura KG
    b) Sitz: Stuttgart Geschäftsanschrift: Königstraße 1, 70173 Stuttgart
    Persönlich haftende Gesellschafterin: Muster Verwaltungs AG, Stuttgart (Amtsgericht Stuttgart, HRB 999)
    4. Prokura: Dr. Kadletz, Andreas, Stuttgart, *09.01.1969 Klatt, Fabian Michael, Stuttgart, *27.04.1986
    5. a) Rechtsform: Kommanditgesellschaft
    Kommanditist(en):
    Meier, Anna Maria, Wien / Österreich, *01.02.1970, Haftsumme: 50.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["natuerliche_phGs"] == [], r["natuerliche_phGs"]
    prok_names = {p["adresse"]["nameKomplett"] for p in r["prokuristen"]}
    assert prok_names == {"Andreas Kadletz", "Fabian Michael Klatt"}, prok_names


@check("distinct people sharing a full name are NOT merged")
def _():
    doc = """
    Nummer der Firma: HRA 500
    2. a) Firma: Familie Schmidt KG
    b) Sitz: Bonn Geschäftsanschrift: Rheinweg 5, 53111 Bonn
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Schmidt, Peter, Bonn, *01.01.1950, Haftsumme: 10.000,00 EUR
    Schmidt, Peter, Köln, *01.01.1985, Haftsumme: 20.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert len(r["kommanditisten_personen"]) == 2, r["kommanditisten_personen"]
    doblist = sorted(k["geburtsdatum"] for k in r["kommanditisten_personen"])
    assert doblist == ["1950-01-01", "1985-01-01"], doblist


@check("true duplicate entry collapses to one")
def _():
    doc = """
    Nummer der Firma: HRA 501
    2. a) Firma: Solo KG
    b) Sitz: Bonn Geschäftsanschrift: Rheinweg 5, 53111 Bonn
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Weber, Klaus, Bonn, *01.01.1960, Haftsumme: 10.000,00 EUR
    Weber, Klaus, Bonn, *01.01.1960, Haftsumme: 10.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert len(r["kommanditisten_personen"]) == 1, r["kommanditisten_personen"]


@check("compound surname particle preserved (von / van / zu)")
def _():
    doc = """
    Nummer der Firma: HRA 600
    2. a) Firma: Adel KG
    b) Sitz: München Geschäftsanschrift: Adelsweg 1, 80331 München
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    von Bülow, Hans, München, *01.01.1960, Haftsumme: 10.000,00 EUR
    van der Berg, Jan, München, *02.02.1965, Haftsumme: 20.000,00 EUR
    zu Guttenberg, Karl, München, *03.03.1970, Haftsumme: 30.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    names = {k["nachname"] for k in r["kommanditisten_personen"]}
    assert names == {"von Bülow", "van der Berg", "zu Guttenberg"}, names


@check("foreign resident: Ort / Land split, ISO country code")
def _():
    doc = """
    Nummer der Firma: HRA 700
    2. a) Firma: International KG
    b) Sitz: Duisburg Geschäftsanschrift: Lanterstraße 20, 46539 Dinslaken
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Hellmich, Walter Georg, Luxemburg / Luxemburg, *24.03.1944, Haftsumme: 1.000.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    k = r["kommanditisten_personen"][0]
    assert k["adresse"]["ort"] == "Luxemburg", k["adresse"]
    assert k["adresse"]["land"] == "LU", k["adresse"]
    assert k["beteiligung"]["share"] == "1000000.00", k["beteiligung"]


@check("legacy DEM currency and Einlage label")
def _():
    doc = """
    Handelsregister A des Amtsgerichts Köln
    Nummer der Firma: HRA 18706
    2. a) Firma: Alt KG
    b) Sitz: Brühl Geschäftsanschrift: Berggeiststrasse 31, 50321 Brühl
    4. Kommanditist(en):
    Löffelhardt, Robert Gottlieb, Brühl, *13.04.1964, Einlage: 2.985.000,00 DEM
    6. a) Tag der letzten Eintragung: 20.12.2022
    """
    r = P.parse_handelsregister_a_text(doc)
    k = r["kommanditisten_personen"][0]
    assert k["beteiligung"]["waehrung"] == "DEM", k["beteiligung"]
    assert k["beteiligung"]["share"] == "2985000.00", k["beteiligung"]


@check("company Kommanditist recognised without GmbH suffix (AG, e.K.)")
def _():
    doc = """
    Nummer der Firma: HRA 800
    2. a) Firma: Vielfalt KG
    b) Sitz: Essen Geschäftsanschrift: Ruhrallee 2, 45128 Essen
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Nordwind AG, Essen (Amtsgericht Essen, HRB 321) Haftsumme: 40.000,00 EUR
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    kg = r["kommanditisten_gesellschaften"]
    assert len(kg) == 1, kg
    assert kg[0]["name"] == "Nordwind AG", kg[0]
    assert kg[0]["beteiligung"]["share"] == "40000.00", kg[0]["beteiligung"]


@check("mixed company + person Kommanditisten in the same block")
def _():
    doc = """
    Nummer der Firma: HRA 900
    2. a) Firma: Mischform KG
    b) Sitz: Flensburg Geschäftsanschrift: Hafenweg 3, 24937 Flensburg
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Andresen, Heike Susann, *19.11.1974, Jübek 4.000,00 EUR
    iTerra Wind GmbH & Co. KG, Risum-Lindholm (Amtsgericht Flensburg, HRA 7709 FL) Hafteinlage: 192.000,00 EUR
    Nielsen, Jörg, *29.08.1972, Klixbüll 128.000,00 EUR
    6. a) Tag der letzten Eintragung: 08.03.2022
    """
    r = P.parse_handelsregister_a_text(doc)
    assert len(r["kommanditisten_personen"]) == 2, r["kommanditisten_personen"]
    assert len(r["kommanditisten_gesellschaften"]) == 1, r["kommanditisten_gesellschaften"]
    assert r["kommanditisten_gesellschaften"][0]["beteiligung"]["share"] == "192000.00"


@check("house-number range and letter suffix in address")
def _():
    doc = """
    Nummer der Firma: HRA 1000
    2. a) Firma: Range KG
    b) Sitz: Brühl Geschäftsanschrift: Berggeiststrasse 31 - 41, 50321 Brühl
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["unternehmen"]["adresse"]["hausnummer"] == "31 - 41"

    doc2 = doc.replace("31 - 41", "20 a")
    r2 = P.parse_handelsregister_a_text(doc2)
    assert r2["unternehmen"]["adresse"]["hausnummer"] == "20 a"


@check("street name containing digits (Straße des 17. Juni)")
def _():
    doc = """
    Nummer der Firma: HRA 1100
    2. a) Firma: Datum KG
    b) Sitz: Stuttgart Geschäftsanschrift: Straße des 17. Juni 20 a, 70173 Stuttgart
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    a = r["unternehmen"]["adresse"]
    assert a["strasse"] == "Straße des 17. Juni", a
    assert a["hausnummer"] == "20 a", a
    assert a["plz"] == "70173", a
    assert a["ort"] == "Stuttgart", a


@check("register-number court suffix kept (HRA 8195 FL) not swallowed")
def _():
    doc = """
    Amtsgericht Flensburg HRA 8195 FL
    2. a) Firma: Suffix KG
    b) Sitz: Flensburg Geschäftsanschrift: Norderdorf 7, 25850 Behrendorf
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["unternehmen"]["handelsregisternummer"] == "HRA 8195 FL", r["unternehmen"]


@check("register number never read off a partner company")
def _():
    doc = """
    Amtsgericht Duisburg
    2. a) Firma: Walter Hellmich Holding GmbH & Co. KG
    b) Sitz: Dinslaken Geschäftsanschrift: Lanterstraße 20, 46539 Dinslaken
    Persönlich haftender Gesellschafter: Walter Hellmich Beteiligungsgesellschaft mbH, Dinslaken (Amtsgericht Duisburg HRB 31291)
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Hellmich, Walter Georg, Luxemburg / Luxemburg, *24.03.1944, Haftsumme: 1.000.000,00 EUR
    6. a) Tag der letzten Eintragung: 07.10.2025
    """
    r = P.parse_handelsregister_a_text(doc)
    # No "Nummer der Firma:" label and no bare HRA in the header here — the
    # company's own number is genuinely absent from this fixture. What matters
    # is that HRB 31291 (the partner's number) is NOT taken as the company's.
    assert r["unternehmen"]["handelsregisternummer"] != "HRB 31291", r["unternehmen"]


@check("legal form derived from company name suffix")
def _():
    doc = """
    Nummer der Firma: HRA 1200
    2. a) Firma: Formtest GmbH & Co. KG
    b) Sitz: Bremen Geschäftsanschrift: Schiffsweg 4, 28195 Bremen
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["unternehmen"]["rechtsform"] == "61", r["unternehmen"]["rechtsform"]


@check("legal form derived from explicit Rechtsform field when name is ambiguous")
def _():
    doc = """
    Nummer der Firma: HRA 1300
    2. a) Firma: Formtest Handels KG
    b) Sitz: Bremen Geschäftsanschrift: Schiffsweg 4, 28195 Bremen
    5. a) Rechtsform: Kommanditgesellschaft
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["unternehmen"]["rechtsform"] == "59", r["unternehmen"]["rechtsform"]


@check("no Prokura section present -> empty list, no crash")
def _():
    doc = """
    Nummer der Firma: HRA 1400
    2. a) Firma: Ohne Prokura KG
    b) Sitz: Kiel Geschäftsanschrift: Hafenstraße 1, 24103 Kiel
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["prokuristen"] == []


@check("HTML entities decoded (&amp; -> &)")
def _():
    doc = """
    Nummer der Firma: HRA 1500
    2. a) Firma: Test &amp; Co. KG
    b) Sitz: Kiel Geschäftsanschrift: Hafenstraße 1, 24103 Kiel
    6. a) Tag der letzten Eintragung: 01.01.2024
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["unternehmen"]["name"] == "Test & Co. KG", r["unternehmen"]["name"]


@check("empty / garbage input never crashes, and returns None via the gate")
def _():
    for bad in ("", "   ", "asdkjhaskjdh not a register document at all", None):
        r = P.parse_handelsregister_a_text(bad)
        assert isinstance(r, dict)
        assert r["persoenlich_haftende_gesellschafter"] == []
        assert r["kommanditisten_personen"] == []
    assert P.parse_handelsregister_a_or_none("garbage text") is None
    assert P.parse_handelsregister_a_or_none("") is None


@check("headings WITHOUT a colon (Aktueller Ausdruck layout)")
def _():
    # Real layout: "c) Kommanditisten, Mitglieder" is printed bare, with no
    # colon, and the list index sits on its own line above each entry.
    doc = """
    3.a) Allgemeine Vertretungsregelung
    Jeder persönlich haftende Gesellschafter vertritt die Gesellschaft allein.
    b) Inhaber, persönlich haftende Gesellschafter, Geschäftsführer, Vorstand, Vertretungsberechtigte
    und besondere Vertretungsbefugnis
    Persönlich haftender Gesellschafter:
    A & B Windenergie GmbH, Nordhackstedt (Amtsgericht Flensburg, HRB 2856 FL)
    5.a) Rechtsform, Beginn und Satzung
    Kommanditgesellschaft
    c) Kommanditisten, Mitglieder
    1.
    Asmussen, Hans-Peter, Handewitt                              306.775,13 EUR
    3.
    Brodersen, Gerrit, *05.09.1973, Nordhackstedt                306.775,13 EUR
    6. Tag der letzten Eintragung
    13.12.2011
    """
    r = P.parse_handelsregister_a_text(doc)
    assert len(r["kommanditisten_personen"]) == 2, r["kommanditisten_personen"]
    names = {k["adresse"]["nameKomplett"] for k in r["kommanditisten_personen"]}
    assert names == {"Hans-Peter Asmussen", "Gerrit Brodersen"}, names
    phg = r["persoenlich_haftende_gesellschafter"]
    assert len(phg) == 1 and phg[0]["name"] == "A & B Windenergie GmbH", phg


@check("Kommanditist with no printed birth date -> geburtsdatum is None")
def _():
    doc = """
    c) Kommanditisten, Mitglieder
    1.
    Asmussen, Hans-Peter, Handewitt                              306.775,13 EUR
    3.
    Brodersen, Gerrit, *05.09.1973, Nordhackstedt                306.775,13 EUR
    6. Tag der letzten Eintragung
    13.12.2011
    """
    r = P.parse_handelsregister_a_text(doc)
    by_name = {k["adresse"]["nameKomplett"]: k for k in r["kommanditisten_personen"]}
    # XML reports null for a missing birth date, so None — not "" — is required.
    assert by_name["Hans-Peter Asmussen"]["geburtsdatum"] is None, by_name["Hans-Peter Asmussen"]
    assert by_name["Gerrit Brodersen"]["geburtsdatum"] == "1973-09-05"


@check("HRB: department detected and routed correctly")
def _():
    hrb = """
    Amtsgericht Flensburg
    - Handelsregister Abteilung B -
    HRB 2856 FL
    2.a) Firma
    A & B Windenergie GmbH
    b) Sitz, Niederlassung, inländische Geschäftsanschrift, empfangsberechtigte Person, Zweigniederlassungen
    Nordhackstedt
    Schauweg 50, 24980 Nordhackstedt
    3. Grund- oder Stammkapital
    50.000,00 DM
    4.a) Allgemeine Vertretungsregelung
    Die Gesellschaft hat einen oder mehrere Geschäftsführer.
    b) Vorstand, Leitungsorgan, geschäftsführende Direktoren, persönlich haftende Gesellschafter,
    Geschäftsführer, Vertretungsberechtigte und besondere Vertretungsbefugnis
    Geschäftsführer:
    mit der Befugnis die Gesellschaft mit einem anderen Geschäftsführer zu vertreten
    Asmussen, Hans P., Handewitt, Landwirt
    Brodersen, Gerrit, *05.09.1973, Nordhackstedt
    6.a) Rechtsform, Beginn, Satzung oder Gesellschaftsvertrag
    Gesellschaft mit beschränkter Haftung
    7. Tag der letzten Eintragung
    24.07.2026
    """
    assert P.detect_register_type(hrb) == "B"

    r = P.parse_handelsregister_text(hrb)
    u = r["unternehmen"]
    assert u["name"] == "A & B Windenergie GmbH", u["name"]
    assert u["handelsregisternummer"] == "HRB 2856 FL", u["handelsregisternummer"]
    # the court city must not absorb the "- Handelsregister Abteilung B -" header
    assert u["registergericht"] == "Flensburg", u["registergericht"]
    assert u["adresse"]["strasse"] == "Schauweg", u["adresse"]
    assert u["adresse"]["plz"] == "24980", u["adresse"]

    names = {x["adresse"]["nameKomplett"] for x in r["leitende_personen"]}
    assert names == {"Hans P. Asmussen", "Gerrit Brodersen"}, names

    by_name = {x["adresse"]["nameKomplett"]: x for x in r["leitende_personen"]}
    # no birth date printed for Asmussen; a profession follows instead, which
    # must not end up in the town field
    assert by_name["Hans P. Asmussen"]["geburtsdatum"] is None
    assert by_name["Hans P. Asmussen"]["adresse"]["ort"] == "Handewitt"
    assert by_name["Gerrit Brodersen"]["geburtsdatum"] == "1973-09-05"

    # an HRB document has no Kommanditisten and no HRA-style partners
    assert r["kommanditisten_personen"] == []
    assert r["persoenlich_haftende_gesellschafter"] == []


@check("HRA still routes to the A parser (no HRB regression)")
def _():
    hra = """
    Amtsgericht Flensburg
    - Handelsregister Abteilung A -
    HRA 8195 FL
    2. a) Firma: Windpark Enleni GmbH & Co. KG
    b) Sitz: Behrendorf Geschäftsanschrift: Norderdorf 7, 25850 Behrendorf
    Persönlich haftender Gesellschafter: Enleni GmbH, Behrendorf (Amtsgericht Flensburg, HRB 10342 FL)
    c) Kommanditisten, Mitglieder
    Andresen, Heike Susann, *19.11.1974, Jübek 4.000,00 EUR
    6. a) Tag der letzten Eintragung: 08.03.2022
    """
    assert P.detect_register_type(hra) == "A"
    r = P.parse_handelsregister_text(hra)
    assert len(r["persoenlich_haftende_gesellschafter"]) == 1
    assert len(r["kommanditisten_personen"]) == 1
    assert r["leitende_personen"] == []


@check("full integration: every section present at once, nothing dropped")
def _():
    doc = """
    Amtsgericht Flensburg HRA 8195 FL
    2. a) Firma: Windpark Enleni GmbH & Co. KG
    b) Sitz: Behrendorf Geschäftsanschrift: Norderdorf 7, 25850 Behrendorf
    b) Inhaber, persönlich haftende Gesellschafter:
    Enleni GmbH, Behrendorf (Amtsgericht Flensburg, HRB 10342 FL)
    Persönlich haftende Gesellschafter: Müller, Hans, Behrendorf, *01.01.1970 und Müller, Petra, Behrendorf, *02.02.1972
    4. Prokura: Dr. Kadletz, Andreas, Flensburg, *09.01.1969 Klatt, Fabian Michael, Flensburg, *27.04.1986
    5. a) Rechtsform: Kommanditgesellschaft
    c) Kommanditisten, Mitglieder:
    Kommanditist(en):
    Andresen, Heike Susann, *19.11.1974, Jübek 4.000,00 EUR
    iTerra Wind GmbH & Co. KG, Risum-Lindholm (Amtsgericht Flensburg, HRA 7709 FL) Hafteinlage: 192.000,00 EUR
    Nielsen, Jörg, *29.08.1972, Klixbüll 128.000,00 EUR
    6. a) Tag der letzten Eintragung: 08.03.2022
    """
    r = P.parse_handelsregister_a_text(doc)
    assert r["unternehmen"]["name"] == "Windpark Enleni GmbH & Co. KG"
    assert r["unternehmen"]["handelsregisternummer"] == "HRA 8195 FL"
    assert len(r["persoenlich_haftende_gesellschafter"]) == 1
    assert len(r["natuerliche_phGs"]) == 2
    assert len(r["prokuristen"]) == 2
    assert len(r["kommanditisten_personen"]) == 2
    assert len(r["kommanditisten_gesellschaften"]) == 1
    # nothing bled across sections
    prok_names = {p["adresse"]["nameKomplett"] for p in r["prokuristen"]}
    natphg_names = {p["adresse"]["nameKomplett"] for p in r["natuerliche_phGs"]}
    assert prok_names.isdisjoint(natphg_names), (prok_names, natphg_names)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    failures = 0
    for name, fn in checks:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}\n      {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}\n      {type(e).__name__}: {e}")

    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    raise SystemExit(1 if failures else 0)
