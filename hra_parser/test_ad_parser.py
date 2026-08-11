"""Self-contained check for ad_parser.py.

Stubs the kyc_assistant imports so the parser can be exercised outside the project,
then asserts the AD output against the reference XJustiz output for two printouts.
"""

import json
import sys
import types
from pathlib import Path

# --- stubs -----------------------------------------------------------------

legal_forms = types.ModuleType("kyc_assistant.domain.legal_forms")


def extract_rechtsform(text: str) -> str:
    text = (text or "").lower()
    if "gmbh & co. kg" in text or "gmbh & co kg" in text:
        return "gmbh_co_kg"
    if "mbh" in text or "gmbh" in text:
        return "gmbh"
    if "kommanditgesellschaft" in text:
        return "kg"
    return ""


legal_forms.extract_rechtsform = extract_rechtsform
legal_forms.rechtsform_map = {"gmbh_co_kg": "61", "gmbh": "60", "kg": "59"}


class _Data:
    plz_bundesland_mapping = {
        "50321": types.SimpleNamespace(bundesland="Nordrhein-Westfalen"),
        "25850": types.SimpleNamespace(bundesland="Schleswig-Holstein"),
    }

    @staticmethod
    def get_bundesland_by_ort(ort):
        return {
            "Köln": "Nordrhein-Westfalen",
            "Brühl": "Nordrhein-Westfalen",
            "Behrendorf": "Schleswig-Holstein",
        }.get(ort, "")


xml_parser = types.ModuleType("kyc_assistant.services.xml_parser")
xml_parser.get_bundesland_data = lambda: _Data()

for name, mod in [
    ("kyc_assistant", types.ModuleType("kyc_assistant")),
    ("kyc_assistant.domain", types.ModuleType("kyc_assistant.domain")),
    ("kyc_assistant.domain.legal_forms", legal_forms),
    ("kyc_assistant.services", types.ModuleType("kyc_assistant.services")),
    ("kyc_assistant.services.xml_parser", xml_parser),
]:
    sys.modules[name] = mod

sys.path.insert(0, str(Path(__file__).parent))
import ad_parser  # noqa: E402

# --- fixtures --------------------------------------------------------------

# "Aktueller Ausdruck" layout, two pages, two-column amounts (the photographed PDF).
FLENSBURG = """Ausdruck
                        - Wiedergabe des aktuellen Registerinhalts -
                        Abruf vom 24.07.2026, 14:02                         HRA 8195 FL
                        Amtsgericht Flensburg
                        - Handelsregister Abteilung A -

Aktueller Ausdruck                                                          HRA 8195 FL

Handelsregister Abteilung A
Amtsgericht Flensburg

1. Anzahl der bisherigen Eintragungen
   4 Eintragung(en)

2.a) Firma
   Windpark Enleni GmbH & Co. KG
b) Sitz, Niederlassung, inländische Geschäftsanschrift, Zweigniederlassungen
   Behrendorf
   Norderdorf 7, 25850 Behrendorf

3.a) Allgemeine Vertretungsregelung
   Jeder persönlich haftende Gesellschafter vertritt die Gesellschaft allein.
b) Inhaber, persönlich haftende Gesellschafter, Geschäftsführer, Vorstand, Vertretungsberechtigte
   und besondere Vertretungsbefugnis
   Persönlich haftender Gesellschafter:
   mit der Befugnis die Gesellschaft allein zu vertreten mit der Befugnis Rechtsgeschäfte mit sich
   selbst oder als Vertreter Dritter abzuschließen
   Enleni GmbH, Behrendorf (Amtsgericht Flensburg, HRB 10342 FL)

5.a) Rechtsform, Beginn und Satzung
   Kommanditgesellschaft
c) Kommanditisten, Mitglieder
   1.
   Andresen, Heike Susann, *19.11.1974, Jübek                       4.000,00 EUR
   2.
   Bade, Inge Petrea, *13.11.1964, Husum                            4.000,00 EUR
   4.
   Clausen, Markus, *04.05.1979, Haselund                           4.000,00 EUR
   5.
   iTerra Wind GmbH & Co. KG, Risum-Lindholm (Amtsgericht Flens-  192.000,00 EUR
   burg, HRA 7709 FL)
   6.
   Johannsen, Maik, *25.09.1974, Großenwiehe                      160.000,00 EUR
   7.
   Lind, Marion Manuela, *22.07.1966, Hattstedt                     4.000,00 EUR
   8.
   Nielsen, Jörg, *29.08.1972, Klixbüll                           128.000,00 EUR
   9.
24.07.2026                                                                  Seite 1 von 2
Ausdruck
                        - Wiedergabe des aktuellen Registerinhalts -
                        Abruf vom 24.07.2026, 14:02                         HRA 8195 FL
                        Amtsgericht Flensburg
                        - Handelsregister Abteilung A -

   Peters, Daniela Ursel, *03.12.1968, Schacht-Audorf                4.000,00 EUR
   10.
   Pietrock, Andrea, *05.09.1969, Struckum                           4.000,00 EUR
   11.
   Siefert, Eugen, *04.01.1967, Behrendorf                         192.000,00 EUR
   12.
   Spingel, Frauke, *16.04.1971, Haselund                            4.000,00 EUR
   13.
   Petersen, Johannes, *29.09.1960, Husum                           96.000,00 EUR
   14.
   Carstensen, Gerd, *26.03.1955, Haselund                           4.000,00 EUR

6. Tag der letzten Eintragung
   08.03.2022
24.07.2026                                                                  Seite 2 von 2
"""

# Older "Ausdruck" layout with inline labels, DEM amounts, house-number range.
PHANTASIALAND = """Handelsregister A des Amtsgerichts Köln
Nummer der Firma: HRA 18706
Allgemeine Angaben

2. a) Firma:
Phantasialand Schmidt-Löffelhardt GmbH & Co. KG
b) Sitz: Brühl
Geschäftsanschrift:
Berggeiststrasse 31 - 41, 50321 Brühl

3. Persönlich haftender Gesellschafter: Phantasialand Verwaltungsgesellschaft mbH, Brühl
(Amtsgericht Köln, HRB 44564)
Persönlich haftender Gesellschafter: Becker, Herta, Köln, *11.05.1957

4. Kommanditist(en):
Löffelhardt, Robert Gottlieb, *13.04.1964, Brühl
2.985.000,00 DEM

5. a) Rechtsform: Kommanditgesellschaft Beginn: 28.08.1995
6. a) Tag der letzten Eintragung: 20.12.2022
Abruf vom 10.08.2026
"""

# Reference output produced by the XJustiz parser for the same two documents.
EXPECTED_FLENSBURG = {
    "unternehmen": {
        "name": "Windpark Enleni GmbH & Co. KG",
        "rechtsform": "61",
        "handelsregisternummer": "HRA 8195 FL",
        "registergericht": "Flensburg",
        "amtsgericht_verbatim": "Amtsgericht Flensburg",
        "geschaeftsfuehrer": "",
        "adresse": {
            "nameKomplett": "Windpark Enleni GmbH & Co. KG",
            "strasse": "Norderdorf",
            "hausnummer": "7",
            "plz": "25850",
            "ort": "Behrendorf",
            "bundesland": "Schleswig-Holstein",
            "land": "DE",
        },
        "eintragungsdatum": "",
        "letzte_aenderung": "",
    },
    "phg_names": [("Enleni GmbH", "HRB 10342 FL", "Flensburg", "Behrendorf")],
    "kg_orgs": [("iTerra Wind GmbH & Co. KG", "HRA 7709 FL", "Flensburg",
                 "Risum-Lindholm", "192000.00", "EUR")],
    "kommanditisten": [
        ("Heike Susann Andresen", "1974-11-19", "Jübek", "4000.00"),
        ("Inge Petrea Bade", "1964-11-13", "Husum", "4000.00"),
        ("Markus Clausen", "1979-05-04", "Haselund", "4000.00"),
        ("Maik Johannsen", "1974-09-25", "Großenwiehe", "160000.00"),
        ("Marion Manuela Lind", "1966-07-22", "Hattstedt", "4000.00"),
        ("Jörg Nielsen", "1972-08-29", "Klixbüll", "128000.00"),
        ("Daniela Ursel Peters", "1968-12-03", "Schacht-Audorf", "4000.00"),
        ("Andrea Pietrock", "1969-09-05", "Struckum", "4000.00"),
        ("Eugen Siefert", "1967-01-04", "Behrendorf", "192000.00"),
        ("Frauke Spingel", "1971-04-16", "Haselund", "4000.00"),
        ("Johannes Petersen", "1960-09-29", "Husum", "96000.00"),
        ("Gerd Carstensen", "1955-03-26", "Haselund", "4000.00"),
    ],
}

EXPECTED_PHANTASIALAND_HL = [
    "Berggeiststrasse 31 - 41, 50321 Brühl",
    "Amtsgerichts Köln",
    "HRA 18706",
    "Phantasialand Schmidt-Löffelhardt GmbH & Co. KG",
    "Brühl",
    "Amtsgericht Köln",
    "HRB 44564",
    "Phantasialand Verwaltungsgesellschaft mbH",
    "Brühl",
    "Robert Gottlieb Löffelhardt",
    "2985000.00 DEM",
    "Abruf vom 10.08.2026",
]

# --- checks ----------------------------------------------------------------

failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}\n   got      {actual!r}\n   expected {expected!r}")


fl = ad_parser.parse_handelsregister_a_text(FLENSBURG)
ph = ad_parser.parse_handelsregister_a_text(PHANTASIALAND)

check("FL unternehmen", fl["unternehmen"], EXPECTED_FLENSBURG["unternehmen"])

check(
    "FL phg",
    [
        (p["name"], p["handelsregisternummer"], p["registergericht"], p["adresse"]["ort"])
        for p in fl["persoenlich_haftende_gesellschafter"]
    ],
    EXPECTED_FLENSBURG["phg_names"],
)

check(
    "FL kommanditisten_gesellschaften",
    [
        (
            k["name"],
            k["handelsregisternummer"],
            k["registergericht"],
            k["adresse"]["ort"],
            k["beteiligung"]["share"],
            k["beteiligung"]["waehrung"],
        )
        for k in fl["kommanditisten_gesellschaften"]
    ],
    EXPECTED_FLENSBURG["kg_orgs"],
)

check(
    "FL kommanditisten_personen",
    [
        (
            k["adresse"]["nameKomplett"],
            k["geburtsdatum"],
            k["adresse"]["ort"],
            k["beteiligung"]["share"],
        )
        for k in fl["kommanditisten_personen"]
    ],
    EXPECTED_FLENSBURG["kommanditisten"],
)

check("FL natuerliche_phGs", fl["natuerliche_phGs"], [])

check("PH handelsregisternummer", ph["unternehmen"]["handelsregisternummer"], "HRA 18706")
check(
    "PH adresse",
    ph["unternehmen"]["adresse"],
    {
        "nameKomplett": "Phantasialand Schmidt-Löffelhardt GmbH & Co. KG",
        "strasse": "Berggeiststrasse",
        "hausnummer": "31 - 41",
        "plz": "50321",
        "ort": "Brühl",
        "bundesland": "Nordrhein-Westfalen",
        "land": "DE",
    },
)
check("PH eintragungsdatum", ph["unternehmen"]["eintragungsdatum"], "")
check("PH letzte_aenderung", ph["unternehmen"]["letzte_aenderung"], "")
check(
    "PH phg ort",
    [(p["name"], p["adresse"]["ort"]) for p in ph["persoenlich_haftende_gesellschafter"]],
    [("Phantasialand Verwaltungsgesellschaft mbH", "Brühl")],
)
check(
    "PH natuerliche_phGs",
    [(p["adresse"]["nameKomplett"], p["geburtsdatum"], p["adresse"]["ort"],
      p["adresse"]["bundesland"]) for p in ph["natuerliche_phGs"]],
    [("Herta Becker", "1957-05-11", "Köln", "Nordrhein-Westfalen")],
)
check(
    "PH kommanditisten_personen",
    [(k["adresse"]["nameKomplett"], k["geburtsdatum"], k["adresse"]["ort"],
      k["beteiligung"]["share"], k["beteiligung"]["waehrung"])
     for k in ph["kommanditisten_personen"]],
    [("Robert Gottlieb Löffelhardt", "1964-04-13", "Brühl", "2985000.00", "DEM")],
)
check("PH to_highlight", ph["to_highlight"], EXPECTED_PHANTASIALAND_HL)

if failures:
    print("FAILURES\n")
    print("\n\n".join(failures))
    print("\n--- Flensburg ---")
    print(json.dumps(fl, ensure_ascii=False, indent=2))
    print("\n--- Phantasialand ---")
    print(json.dumps(ph, ensure_ascii=False, indent=2))
    sys.exit(1)

print("all checks passed")
print("\nFlensburg to_highlight:")
for value in fl["to_highlight"]:
    print("  -", value)
