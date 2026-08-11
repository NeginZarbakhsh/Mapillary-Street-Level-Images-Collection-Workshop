# HRA "Aktueller Ausdruck" parser — XML parity fixes

`ad_parser.py` is a drop-in replacement for the Handelsregister-A PDF parser
(`parse_handelsregister_a_text` / `parse_handelsregister_a_or_none`). It belongs in
the `kyc_assistant` project next to `services/xml_parser.py`; it lives here only
because that project is not part of this repository.

`test_ad_parser.py` stubs `kyc_assistant.domain.legal_forms` and
`kyc_assistant.services.xml_parser`, so it runs standalone:

```
python3 hra_parser/test_ad_parser.py
```

It asserts the parser output for two printouts against the reference XJustiz output:
the Flensburg "Aktueller Ausdruck" (HRA 8195 FL, numbered headings, two-column
amounts, page break mid-list) and the older Köln "Ausdruck" (HRA 18706, inline
labels, DEM amounts, house-number range).

## What was causing each mismatch

| Field | Cause | Fix |
| --- | --- | --- |
| `handelsregisternummer` (`HRA 18706 A`) | `\s*[A-Z]{0,3}` after the number crossed the newline and ate the `A` of the next heading | suffix must be a standalone token on the same line, so `HRA 8195 FL` still keeps `FL` |
| `adresse.strasse/hausnummer/plz` | `(?P<hausnummer>\d+\w?)` cannot match the range `31 - 41` | house numbers accept ranges and letter suffixes |
| `adresse.bundesland` | never reached, because the address regex above failed | falls out once the PLZ parses |
| `persoenlich_haftende_gesellschafter[].adresse.ort` | the captured city was discarded ("XML leaves PHG ort empty" — it does not) | city is written to the record |
| `kommanditisten_personen` | currency was hard-coded to `EUR`; the document uses `DEM` | any 2–3 letter currency code, amount on the same line or the next |
| `eintragungsdatum` / `letzte_aenderung` | AD filled them, XML left them empty | gated behind `EMIT_REGISTRATION_DATES` (see below) |
| `to_highlight` | different order, natural-person PHGs added, de-duplication | rebuilt in the XML parser's order, duplicates kept |
| `kommanditisten_gesellschaften` | not implemented | company Kommanditisten are now parsed |

## Two things that need your decision

**`EMIT_REGISTRATION_DATES = False`.** The XML parser returns `""` for
`eintragungsdatum` and `letzte_aenderung` even though the message carries
`<gruendungsdatum>` and `<letzteEintragung>`. The AD parser reads both dates
correctly, so parity was only achievable by suppressing them. This looks like a gap
on the XML side — once `xml_parser` populates the two fields, flip this flag to
`True` and the comparison stays green with real data instead of blanks.

**`kommanditisten_gesellschaften` shape.** The reference sample has this list empty,
so the exact record shape the XML parser emits is unverified. It is currently
mirrored from `persoenlich_haftende_gesellschafter` plus
`beteiligung: {share, waehrung}`, and its `to_highlight` entries follow the
partner-company pattern (`Amtsgericht …`, register number, name, seat) in document
order. Check this against an XML message that actually has a company as limited
partner — `HRA 8195 FL` (iTerra Wind GmbH & Co. KG) is one.

## Not fixable in code

In the Köln comparison the retrieval dates genuinely differ between the two
documents — the XML was pulled on `10.08.2026`, the PDF on `24.07.2026`. The
`Abruf vom …` highlight will not match until both sides come from the same
retrieval.
