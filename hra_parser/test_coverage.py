"""Regression suite for the three-level completeness checks.

    python3 test_coverage.py

The point of these checks is not that coverage reports 100% — that is easy and
worthless. It is that coverage DROPS when data is genuinely lost. A metric
that stays green while entries go missing is worse than no metric, so every
check here breaks the parser deliberately and asserts the number falls.
"""

import re
import sys
import types

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

    class _B:
        plz_bundesland_mapping = {}

        @staticmethod
        def get_bundesland_by_ort(_o):
            return ""

    _xp = types.ModuleType("kyc_assistant.services.xml_parser")
    _xp.get_bundesland_data = lambda: _B()

    for _n, _m in [
        ("kyc_assistant", types.ModuleType("kyc_assistant")),
        ("kyc_assistant.domain", types.ModuleType("kyc_assistant.domain")),
        ("kyc_assistant.domain.legal_forms", _lf),
        ("kyc_assistant.services", types.ModuleType("kyc_assistant.services")),
        ("kyc_assistant.services.xml_parser", _xp),
    ]:
        sys.modules.setdefault(_n, _m)

import hr_a_regex_parser as P  # noqa: E402
from ad_sections import normalize_ad_text, text_coverage  # noqa: E402

# A real two-page register, page-2 running header included.
DOC = normalize_ad_text("""
Ausdruck
- Wiedergabe des aktuellen Registerinhalts -
Abruf vom 24.07.2026, 14:02                       HRA 8195 FL
Amtsgericht Flensburg
- Handelsregister Abteilung A -
2.a) Firma
Windpark Enleni GmbH & Co. KG
b) Sitz, Niederlassung, inländische Geschäftsanschrift, Zweigniederlassungen
Behrendorf
Norderdorf 7, 25850 Behrendorf
3.a) Allgemeine Vertretungsregelung
Jeder persönlich haftende Gesellschafter vertritt die Gesellschaft allein.
b) Inhaber, persönlich haftende Gesellschafter
Persönlich haftender Gesellschafter:
Enleni GmbH, Behrendorf (Amtsgericht Flensburg, HRB 10342 FL)
5.a) Rechtsform, Beginn und Satzung
Kommanditgesellschaft
c) Kommanditisten, Mitglieder
1.
Andresen, Heike Susann, *19.11.1974, Jübek           4.000,00 EUR
2.
Nielsen, Jörg, *29.08.1972, Klixbüll               128.000,00 EUR
3.
24.07.2026                                           Seite 1 von 2
Ausdruck
- Wiedergabe des aktuellen Registerinhalts -
Abruf vom 24.07.2026, 14:02                       HRA 8195 FL
Amtsgericht Flensburg
- Handelsregister Abteilung A -
Peters, Daniela Ursel, *03.12.1968, Schacht-Audorf    4.000,00 EUR
4.
Carstensen, Gerd, *26.03.1955, Haselund              4.000,00 EUR
6. Tag der letzten Eintragung
08.03.2022
""")

checks = []


def check(name):
    def register(fn):
        checks.append((name, fn))
        return fn
    return register


@check("healthy parser: every line accounted for")
def _():
    parsed = P.parse_handelsregister_text(DOC)
    assert len(parsed["kommanditisten_personen"]) == 4, parsed["kommanditisten_personen"]

    report = text_coverage(DOC, parsed)
    assert report["unaccounted"] == [], report["unaccounted"]
    assert report["pct_accounted"] == 100.0, report["pct_accounted"]


@check("coverage DROPS when a page-break loses entries")
def _():
    saved = P._KOMM_SECTION_RE
    # Re-introduce the page-break bug: "Abruf vom" sits in every page header,
    # so allowing it to close the section truncates the list at the break.
    P._KOMM_SECTION_RE = re.compile(
        saved.pattern.replace(r"|\Z)", r"|\s*Abruf vom|\Z)"), re.S)
    try:
        parsed = P.parse_handelsregister_text(DOC)
        assert len(parsed["kommanditisten_personen"]) < 4, "bug should lose entries"

        report = text_coverage(DOC, parsed)
        assert report["pct_accounted"] < 100.0, report["pct_accounted"]

        lost = " ".join(i["line"] for i in report["unaccounted"])
        assert "Peters" in lost, report["unaccounted"]
        assert "Carstensen" in lost, report["unaccounted"]
    finally:
        P._KOMM_SECTION_RE = saved


@check("coverage DROPS when the section heading is not recognised")
def _():
    saved = P._KOMM_SECTION_RE
    P._KOMM_SECTION_RE = re.compile(
        r"Kommanditist(?:en|\(en\))?\s*:\s*(?P<block>.*?)"
        r"(?=\s*\d+\s*\.\s*[a-z]?\)?\s*Tag der letzten Eintragung|\Z)", re.S)
    try:
        parsed = P.parse_handelsregister_text(DOC)
        assert parsed["kommanditisten_personen"] == []

        report = text_coverage(DOC, parsed)
        assert report["pct_accounted"] < 80.0, report["pct_accounted"]
        assert len(report["unaccounted"]) >= 4, report["unaccounted"]
    finally:
        P._KOMM_SECTION_RE = saved


@check("a shared town does not make a lost person look extracted")
def _():
    # Two people in Haselund; drop one. The surviving one puts "Haselund" in
    # the output, so a naive "is any output string on this line" test would
    # call the lost person's line used and report 100% with data missing.
    doc = normalize_ad_text("""
    Amtsgericht Flensburg   HRA 1
    2.a) Firma
    Test GmbH & Co. KG
    c) Kommanditisten, Mitglieder
    Spingel, Frauke, *16.04.1971, Haselund      4.000,00 EUR
    Carstensen, Gerd, *26.03.1955, Haselund     4.000,00 EUR
    6. Tag der letzten Eintragung
    08.03.2022
    """)
    parsed = P.parse_handelsregister_text(doc)
    assert len(parsed["kommanditisten_personen"]) == 2

    # simulate losing exactly one of them
    parsed["kommanditisten_personen"] = [
        k for k in parsed["kommanditisten_personen"]
        if k["nachname"] != "Carstensen"
    ]

    report = text_coverage(doc, parsed)
    lost = " ".join(i["line"] for i in report["unaccounted"])
    assert "Carstensen" in lost, report["unaccounted"]


@check("boilerplate prose and table headers do not pollute the report")
def _():
    parsed = P.parse_handelsregister_text(DOC)
    report = text_coverage(DOC, parsed)

    reasons = {i["why"] for i in report["skipped"]}
    assert reasons, "nothing was classified as skipped"

    # the representation-rule sentence must be explained, not left unaccounted
    unaccounted = " ".join(i["line"] for i in report["unaccounted"])
    assert "vertritt" not in unaccounted.lower(), report["unaccounted"]


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
