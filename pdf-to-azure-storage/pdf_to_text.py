"""Batch-convert a folder of PDFs to plain text files, locally.

This runs entirely on your own machine, costs nothing, and needs no Azure
account and no Anthropic API key. It's step 1 of the pipeline: get every PDF
into plain text before anything else happens.

Usage:
    python3 pdf_to_text.py                       # uses ./pdfs -> ./text_output
    python3 pdf_to_text.py --in mydocs --out out  # custom folders

Each PDF becomes one .txt file with the same name, plus a "[page N]" marker
before each page's text -- so if you need to cite a page later, it's still in
there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pypdf import PdfReader


def pdf_to_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))

    if reader.is_encrypted:
        try:
            reader.decrypt("")  # many "protected" PDFs use an empty password
        except Exception:
            raise RuntimeError(f"{pdf_path.name}: password-protected, skipping")

    pages = []
    for i, page in enumerate(reader.pages, start=1):
        pages.append(f"[page {i}]\n{page.extract_text() or ''}")

    text = "\n\n".join(pages)
    letters_only = text.replace(" ", "").replace("\n", "")
    if len(letters_only) < 200:
        raise RuntimeError(
            f"{pdf_path.name}: almost no text extracted -- this is likely a "
            "scanned image PDF and needs OCR, which this script doesn't do."
        )
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_dir", default="pdfs", help="Folder of PDFs to read (default: ./pdfs)")
    parser.add_argument("--out", dest="out_dir", default="text_output", help="Folder to write .txt files to (default: ./text_output)")
    args = parser.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(in_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {in_dir}/ -- put your files there and re-run.", file=sys.stderr)
        sys.exit(1)

    ok, failed = 0, 0
    for pdf_path in pdfs:
        out_path = out_dir / (pdf_path.stem + ".txt")
        try:
            text = pdf_to_text(pdf_path)
        except Exception as exc:
            print(f"  SKIPPED  {pdf_path.name}: {exc}")
            failed += 1
            continue
        out_path.write_text(text, encoding="utf-8")
        print(f"  OK       {pdf_path.name} -> {out_path} ({len(text):,} chars)")
        ok += 1

    print(f"\n{ok} converted, {failed} skipped. Text files are in {out_dir}/")


if __name__ == "__main__":
    main()
