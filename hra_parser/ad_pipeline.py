"""One entry point: Handelsregister-A PDF in, parsed record out.

    from ad_pipeline import parse_ad_pdf

    result = parse_ad_pdf("AD.pdf")
    result.data     # the schema dict, from hr_a_regex_parser
    result.text     # the normalised text the parser actually saw
    result.audit    # lines carrying data that the parser did not extract
    print(result.report())

Stages, in order:

1. Read every page. Tables are detected and turned into plain comma-separated
   lines *before* the surrounding text is read, so a table never reaches the
   regex layer as a table — the parser only ever sees prose.
2. Normalise: drop page furniture, repair words broken across lines, collapse
   column padding.
3. Run hr_a_regex_parser unchanged.
4. Audit: report any line holding a birth date, an amount or a register number
   that is missing from the output, labelled with the section it came from.

Text extraction tries pdfplumber, then PyMuPDF, then pdfminer, and uses whichever
is installed. A str or a pre-extracted text file can be passed instead of a PDF,
so this also works downstream of an existing extraction step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ad_sections import audit_extraction, format_audit, normalize_ad_text, split_sections
from hr_a_regex_parser import parse_handelsregister_a_text


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _rows_to_lines(rows) -> list[str]:
    """Render extracted table rows as comma-separated prose lines."""
    lines = []

    for row in rows or []:
        cells = [
            re.sub(r"\s+", " ", (cell or "")).strip()
            for cell in row
        ]
        cells = [c for c in cells if c]
        if cells:
            lines.append(", ".join(cells))

    return lines


def _extract_pdfplumber(path: Path) -> str | None:
    try:
        import pdfplumber
    except ImportError:
        return None

    out: list[str] = []

    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            try:
                tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
            except Exception:
                tables = []

            if not tables:
                out.append(page.extract_text(layout=True) or "")
                continue

            # Walk the page top to bottom, reading the text between tables and
            # flattening each table where it sits. Appending tables at the end
            # would move their rows into whatever section follows.
            cursor = 0.0

            for table in tables:
                top, bottom = table.bbox[1], table.bbox[3]

                if top > cursor:
                    out.append(_crop_text(page, cursor, top))

                try:
                    out.extend(_rows_to_lines(table.extract()))
                except Exception:
                    pass

                cursor = bottom

            if cursor < page.height:
                out.append(_crop_text(page, cursor, page.height))

    return "\n".join(out)


def _crop_text(page, top: float, bottom: float) -> str:
    """Text of one horizontal band of a page."""
    if bottom - top < 1:
        return ""
    try:
        band = page.crop((0, max(0, top), page.width, min(page.height, bottom)))
        return band.extract_text(layout=True) or ""
    except Exception:
        return ""


def _extract_pymupdf(path: Path) -> str | None:
    try:
        import fitz
    except ImportError:
        return None

    out: list[str] = []

    with fitz.open(str(path)) as doc:
        for page in doc:
            try:
                for table in page.find_tables():
                    out.extend(_rows_to_lines(table.extract()))
            except Exception:
                pass
            out.append(page.get_text("text") or "")

    return "\n".join(out)


def _extract_pdfminer(path: Path) -> str | None:
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return None

    return extract_text(str(path)) or ""


_EXTRACTORS = (_extract_pdfplumber, _extract_pymupdf, _extract_pdfminer)


def extract_pdf_text(path) -> str:
    """Read a PDF to text, flattening any detected tables into prose lines."""
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(path)

    errors = []

    for extractor in _EXTRACTORS:
        try:
            text = extractor(path)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            # Native-extension failures can surface as BaseException (pyo3 panics),
            # which must not stop the next extractor from being tried.
            errors.append(f"{extractor.__name__}: {exc}")
            continue

        if text and text.strip():
            return text

    raise RuntimeError(
        "Could not extract text from "
        f"{path.name}. Install pdfplumber or PyMuPDF. Tried: {'; '.join(errors) or 'none'}"
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclass
class AdResult:
    data: dict
    text: str
    raw_text: str
    audit: dict
    source: str = ""
    sections: list = field(default_factory=list)

    @property
    def missed(self) -> list:
        return self.audit.get("missed", [])

    @property
    def complete(self) -> bool:
        """True when no data-bearing line was left unextracted."""
        return not self.audit.get("missed") and not self.audit.get("empty_sections")

    def report(self) -> str:
        head = [
            f"source : {self.source}",
            f"firma  : {self.data['unternehmen']['name']!r}",
            f"hr-nr  : {self.data['unternehmen']['handelsregisternummer']!r}",
            f"phg    : {len(self.data['persoenlich_haftende_gesellschafter'])}"
            f"  natPHG: {len(self.data['natuerliche_phGs'])}"
            f"  prok: {len(self.data['prokuristen'])}"
            f"  komm-P: {len(self.data['kommanditisten_personen'])}"
            f"  komm-G: {len(self.data['kommanditisten_gesellschaften'])}",
            "",
        ]
        return "\n".join(head) + format_audit(self.audit)


def parse_ad_pdf(source) -> AdResult:
    """Parse an AD document end to end.

    ``source`` may be a path to a PDF, a path to an already-extracted .txt/.md
    file, or the text itself.
    """
    raw = _read_source(source)

    text = normalize_ad_text(raw)
    data = parse_handelsregister_a_text(text)
    audit = audit_extraction(text, data)

    return AdResult(
        data=data,
        text=text,
        raw_text=raw,
        audit=audit,
        source=str(source)[:120] if not isinstance(source, str) or len(str(source)) < 200 else "<text>",
        sections=split_sections(text),
    )


def _read_source(source) -> str:
    if isinstance(source, Path) or (
        isinstance(source, str) and "\n" not in source and len(source) < 400
    ):
        path = Path(source)
        if path.exists():
            if path.suffix.lower() == ".pdf":
                return extract_pdf_text(path)
            return path.read_text(encoding="utf-8", errors="replace")

    if isinstance(source, str):
        return source

    raise TypeError(f"Cannot read source of type {type(source).__name__}")


def parse_ad_pdf_to_schema(input_path) -> dict | None:
    """Drop-in replacement for the existing entry point: returns the schema dict.

    Returns None when the minimum company fields are missing, matching the
    behaviour of parse_handelsregister_a_or_none.
    """
    result = parse_ad_pdf(input_path)
    company = result.data.get("unternehmen", {})

    if not all(company.get(k) for k in ("name", "handelsregisternummer", "registergericht")):
        return None

    return result.data


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        result = parse_ad_pdf(arg)
        print("=" * 78)
        print(result.report())
        print()
