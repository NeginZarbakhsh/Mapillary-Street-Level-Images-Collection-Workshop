# How `hr_a_regex_parser.py` works

This explains the parser in plain language: what each function does, how it
decides "this text is a company" versus "this text is a person," and why each
piece exists. No prior regex knowledge assumed — every pattern is translated
into a sentence before it's shown as code.

Companion file: `test_hr_a_regex_parser.py` — 22 automated checks that prove
the claims in this document are actually true. Run it any time:

```
python3 test_hr_a_regex_parser.py
```

It should print `22/22 checks passed`. If you ever change the parser and a
check goes red, that check's name tells you exactly which behaviour broke.

---

## 1. The big picture

One function does the real work: **`parse_handelsregister_a_text(text)`**.
You give it the raw text of a Handelsregister A printout (a German commercial
register "Ausdruck"), and it gives back a Python dictionary with every field
your project's schema expects — company info, general partners, limited
partners, Prokuristen, etc.

It works in two passes:

1. **Find the boundaries.** Before extracting anything, it locates *where*
   in the text each section lives — where the partners section starts and
   ends, where the Kommanditisten section starts and ends. This matters
   because the same word ("GmbH", a birth date, a register number) can appear
   in more than one section, and without boundaries the parser could
   attribute a partner's own register number to the company, or count a
   Prokurist as a general partner.
2. **Extract within each boundary.** Once it knows "this stretch of text is
   the Kommanditisten list," it runs a pattern over just that stretch,
   pulling out every match — every person, every company — not just the
   first one.

Everything below walks through both passes in the order they run.

---

## 2. The building blocks (defined once, reused everywhere)

These are small, named regex fragments assembled near the top of the file.
Think of them as vocabulary — the real patterns further down are built by
gluing these together, so you only need to understand each piece once.

### `_HR_NUMBER` — what a register number looks like

```python
_HR_NUMBER = r"(?:HRA|HRB|GnR|PR|VR)\s*\d+(?:\s+[A-ZÄÖÜ]{1,3})?"
```

In plain terms: *"HRA" or "HRB" (or a couple of rarer register types), then
some digits, then optionally a 1–3 letter court suffix.* This one fragment
recognizes `HRA 8195`, `HRB 10342`, and `HRA 8195 FL` (the `FL` is the
suffix some courts print) — all with the same rule.

### `_ORG_WITH_REGISTER` — **this is how a company is detected**

```python
_ORG_WITH_REGISTER = re.compile(
    r"(?P<name>[^,():]+?)\s*,\s*"
    r"(?P<ort>[^,():]+?)\s*"
    r"\(\s*(?:(?P<court>(?:Amtsgericht|AG)\s+[^,()]*?)\s*,?\s*)?"
    rf"(?P<hr>{_HR_NUMBER})\s*\)"
)
```

In plain terms: *some text (the name), a comma, some more text (the town), an
opening parenthesis, optionally "Amtsgericht" and a court name, a register
number, a closing parenthesis.* That shape —

```
Enleni GmbH, Behrendorf (Amtsgericht Flensburg, HRB 10342 FL)
```

— is exactly what a partner company looks like in every layout this file
handles. The key design choice: **a company is recognised by the register
reference in parentheses, not by seeing "GmbH" in the name.** That's
deliberate — an `AG`, an `e.K.`, a `Stiftung & Co. KG`, or any other legal
form is matched the same way, because the pattern never actually checks the
legal-form word. Only the shape `Name, Town (…HR-number…)` matters.

This one pattern is reused for **three different lists** later:
persönlich haftende Gesellschafter (company form), Kommanditisten (company
form), and it's what tells the parser "skip this, it's a company" when
scanning for people.

### `_PERSON_WITH_DOB` and `_PERSON_ANY` — **this is how a person is detected**

A person entry always has the shape *Surname, Given name(s), Town, \*Birth
date* — but the town and the birth date can swap order depending on which
printout layout produced the document:

```
Andresen, Heike Susann, *19.11.1974, Jübek        ← date, then town
Löffelhardt, Robert Gottlieb, Brühl, *13.04.1964   ← town, then date
```

So the pattern is built as **two alternatives**, tried in order:

```python
_PERSON_WITH_DOB = _NAME_HEAD + (
    r"(?:"
    rf"\*(?P<dob1>{_DATE})\s*,?\s*(?P<ort1>{_PLACE})"   # date-then-town
    r"|"
    rf"(?P<ort2>{_PLACE})\s*,?\s*\*(?P<dob2>{_DATE})"   # town-then-date
    r")"
)
```

`_NAME_HEAD` is the *"Surname, Given name(s),"* part shared by every person
pattern:

```python
_NAME_HEAD = (
    rf"(?P<nachname>{_SURNAME_PARTICLE}[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-']*)\s*,\s*"
    r"(?P<vorname>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-'. ]*?)\s*,\s*"
)
```

`_SURNAME_PARTICLE` handles German surnames with a lower-case prefix word —
`von Bülow`, `van der Berg`, `zu Guttenberg` — so the "von" doesn't get
silently dropped.

**Why the birth date is required, not optional**, is worth spelling out,
because it's the single most important design decision for "detecting a
person" in this file: if the date were optional, the town group (which is
deliberately loose, since town names vary a lot) could match almost anything,
and a stray fragment of text could look like a fake person with no birth
date. Requiring `*DD.MM.YYYY` to actually appear is what keeps company names
and other prose from accidentally being read as people.

`_PERSON_ANY` is the same idea with a third alternative — no date at all —
but it's **only ever used for the Kommanditisten list**, where a money amount
always follows and acts as the real anchor instead of the date. Using
`_PERSON_ANY` anywhere else would be unsafe.

---

## 3. The helper functions

Small, single-purpose functions. Each is used by the bigger extraction logic
further down.

| Function | What it does | Example |
|---|---|---|
| `_norm(text)` | Un-escapes HTML entities (`&amp;` → `&`), collapses repeated spaces/tabs, trims the ends. | `"Test &amp;  Co."` → `"Test & Co."` |
| `_to_iso_date(s)` | German date → ISO date. Returns `""` if it can't parse. | `"19.11.1974"` → `"1974-11-19"` |
| `_german_money_to_en(s)` | German-formatted money (dot = thousands, comma = decimal) → plain decimal string. | `"1.000.000,00"` → `"1000000.00"` |
| `_legal_form_number(text)` | Looks up the project's numeric code for a legal form, using your existing `legal_forms` module. | `"... GmbH & Co. KG"` → `"61"` |
| `_clean_company_name(name)` | Strips leftover fragments (a stray amount, a list index, a repeated role label, the words "Gesellschaft mit beschränkter Haftung") that regex capture sometimes drags in at the edges. | `"1. Enleni GmbH"` → `"Enleni GmbH"` |
| `_append_hl(out, value)` | Adds a value to `to_highlight`, skipping empty strings and exact duplicates. | — |
| `_get_bundesland(city, plz)` | Looks up the German state for a city, preferring postal code, then city name, with a small built-in fallback table for cities not in the shared dataset. | `"Flensburg"` → `"Schleswig-Holstein"` |
| `_split_ort_land(raw)` | Splits a printed residence into town + ISO country code. Foreign residents print as `"Town / Country"`; domestic ones are just a town. | `"Luxemburg / Luxemburg"` → `("Luxemburg", "LU")` |
| `_court_city(raw)` | Strips the word "Amtsgericht"/"AG" off a court reference, leaving just the city. | `"Amtsgericht Flensburg"` → `"Flensburg"` |
| `_person_from_match(m)` | Given a regex match from one of the person patterns, pulls out first name, last name, town, country, and ISO birth date — handling the fact that the town/date could have matched in either of two alternative positions. | — |
| `_person_record(...)` | Builds one person dictionary in the exact schema shape (`adresse`, `beteiligung`, etc.) — used for Prokuristen, natural-person partners, and Kommanditisten alike, so the shape is identical everywhere a person is recorded. | — |
| `_org_record(...)` | Same idea, but for a company entry. | — |

---

## 4. Walking through `parse_handelsregister_a_text`, section by section

The function processes the document in the same order the register itself is
laid out. Each numbered step below corresponds to a numbered comment block in
the code.

### Step 0 — Find section boundaries first

Before anything is extracted, the function finds:

- **Where the Kommanditisten section starts and ends** (`komm_block`,
  `komm_start`) — by searching for the label `Kommanditist(en):` or the
  heading `Kommanditisten, Mitglieder:`, and reading forward until it hits
  "Tag der letzten Eintragung," "Abruf vom," or the end of the document.
- **Where the partners section starts** (`phg_marker`) — by searching for
  `Persönlich haftende Gesellschafter:` or the heading `b) Inhaber`.
- **Where the partners section ends** (`phg_end`) — by searching forward from
  there for the *next* real section: `Prokura:`, `Kommanditist`,
  `Rechtsform`, etc. This stop point matters a lot: a `4. Prokura:` block
  commonly sits between the general-partners text and the Kommanditisten
  list, and without this boundary, Prokuristen would get counted as general
  partners too.

Everything from here on works *within* these boundaries — never across them.
This is also why a partner's own register number can never accidentally
become the company's: the fallback search for the company's number
deliberately excludes both partner regions.

### Step 1 — Court and the company's own register number

Looks for `Amtsgericht <city>` for the court, and the register number in this
order of preference:

1. `Nummer der Firma: HRA nnnn` (explicit label — most reliable)
2. A bare `HRA`/`HRB` number, but **only** in the text *before* the partners
   section (so a partner's number can't win)
3. As an absolute last resort, anywhere in the document **except** inside
   either partner section

### Step 2 — Company name

Reads the text between `2. a) Firma:` and the following `b)` heading.

### Step 3 — Seat and business address

Two attempts, in order: a labelled `Geschäftsanschrift:` pattern first, then a
looser fallback pattern for documents that print the address without that
exact label. The address pattern handles house-number ranges (`31 - 41`),
letter suffixes (`20 a`), and street names that themselves contain digits
(`Straße des 17. Juni`).

### Step 4 — Legal form and dates

The legal form comes from the company name if it's unambiguous (e.g. ends in
"GmbH & Co. KG"); otherwise it falls back to the explicit `Rechtsform:` field.
`eintragungsdatum` and `letzte_aenderung` are deliberately left blank — a
`#*****` comment in the code flags this as intentional, to match what the XML
side currently returns, not a bug.

### Step 4b — Prokuristen (people with power of attorney)

Isolates the `Prokura:` block, then scans the **entire block** for every
person match — not just the first one. This block-scan design (find the
section once, then find every match inside it) is the pattern the rest of the
file follows for every list.

### Step 5 — General partners, company form

Scans the partners block (from Step 0) for every match of
`_ORG_WITH_REGISTER` — i.e. every `Name, Town (…HR-number…)` shape. Any
number of company partners are picked up, because the label doesn't need to
repeat for each one.

### Step 5b — General partners, natural-person form

Scans the **same** partners block for every person match. A birth date is
required — this is what stops the label text itself, or a company entry, from
being mistaken for a person. This scan is not anchored to the label
appearing right before each name, so a document listing several people under
one shared label (`"...Müller, Hans, ... und Müller, Petra, ..."`) still
gets every one of them.

### Step 6 — Kommanditisten (limited partners)

Two passes over the Kommanditisten block found in Step 0:

- **Person pass**: every `_PERSON_ANY` + money match. The date is optional
  here specifically because a money amount always follows and anchors the
  end of the entry — company names like `iTerra Wind GmbH & Co. KG,
  Risum-Lindholm (...)` don't accidentally match, because there's no comma
  right after a single word the way `_NAME_HEAD` requires.
- **Company pass**: every `_ORG_WITH_REGISTER` + optional money match.

Both accept the capital contribution under any of its common labels
(`Haftsumme:`, `Haftein­lage:`, `Einlage:`, `Kapitalanteil:`,
`Kommanditeinlage:`) or with no label at all, and any currency code — not
just EUR, so pre-euro `DEM` entries are captured too.

### Duplicate protection

Every list append is guarded by a check: is this the *same* entry already
recorded? The check compares **name + birth date + town together**, not name
alone — two different real people who happen to share a name (a father and
son, say) are kept as two separate entries; only a genuinely repeated match
(identical name, date, and town) collapses to one.

---

## 5. `parse_handelsregister_a_or_none`

A thin wrapper. Calls the main function, then checks that the company's name,
register number, and court were all found. If any are missing, it prints a
debug line naming which fields are missing and returns `None` instead of a
half-filled result — a signal to whatever calls it that this document didn't
parse cleanly enough to trust.

---

## 6. What this file has been checked against

`test_hr_a_regex_parser.py` runs 22 checks covering:

- Multiple company partners and multiple natural-person partners in one
  document, including the "one label, several people" layout
- Prokuristen never leaking into the partners list, and vice versa
- Two different real people who share a name (kept separate) versus a truly
  duplicated entry (collapsed to one)
- Compound surnames (`von`, `van`, `zu`, …)
- Foreign residents (`Ort / Land` split into town + ISO country code)
- Legacy `DEM` currency and the `Einlage:` label
- A company Kommanditist with no `GmbH` in its name at all
- Company and person Kommanditisten mixed in the same list
- House-number ranges, letter suffixes, and a street name containing digits
- The register-number court suffix (`HRA 8195 FL`) surviving intact
- A partner's register number never being mistaken for the company's own
- Legal form resolved from the company name vs. from an explicit field
- HTML-entity decoding
- Empty and garbage input never raising an exception
- A full document exercising every section at once, checked for cross-section
  leakage

Every one of these was a real, reproduced failure at some point while
building this file — not a hypothetical. Run the suite, and if it's green,
every behaviour on this list is currently true of the code you have.

## 7. What this file does *not* claim to fix

- **Text that never reaches this parser in the first place.** If the string
  passed in has already lost content — because a PDF table wasn't flattened,
  or a page was skipped during extraction — no regex here can recover it.
  That failure happens upstream, before this file ever sees the text.
- **A layout genuinely not covered above.** The patterns here handle every
  variant seen so far in this project. A new government-issued layout with a
  structurally different partner listing could still slip past undetected —
  the way to check is to run a document through and see whether the expected
  entities came out, not to assume.
