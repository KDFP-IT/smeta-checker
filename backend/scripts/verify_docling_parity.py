"""Per-page Docling vs legacy extraction parity report.

Extracts the requested original PDF pages through ``docling_client`` (and,
with ``--legacy``, through ``pdf_text.PDFTextSource``) and prints a Markdown
comparison table followed by the same records as JSON.

Failures are explicit: a missing Docling page aborts with exit code 2 instead
of reporting an empty page as evidence of absence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LEGACY_CACHE_DIR = ".audit_cache/docling-parity"


def _parse_pages(text: str) -> list[int]:
    """Parse ``64,67-69,78-89`` into ascending one-based page numbers."""
    selected: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            selected.update(range(int(start), int(end) + 1))
        else:
            selected.add(int(part))
    if not selected:
        raise ValueError("no page numbers selected")
    return sorted(selected)


def _docling_record(pdf_bytes: bytes, filename: str, page: int) -> dict:
    from docling_client import convert_pages
    from docling_source import docling_pages

    started = time.perf_counter()
    raw = convert_pages(pdf_bytes, filename=filename, page_range=(page, page))
    prepared = docling_pages(raw, sort_by_position=True)
    elapsed = time.perf_counter() - started
    if not prepared:
        print(f"page {page}: Docling returned no pages", file=sys.stderr, flush=True)
        raise SystemExit(2)
    page_doc = next(
        (item for item in prepared if item.get("page") == page), prepared[0]
    )
    text = page_doc.get("text") or ""
    tables = page_doc.get("tables") or []
    return {
        "page": page,
        "docling_s": round(elapsed, 2),
        "legacy_s": None,
        "docling_chars": len(text),
        "legacy_chars": None,
        "docling_tables": len(tables),
        "legacy_tables": None,
        "words": len(text.split()),
        "cells": sum(len(table.get("cells") or []) for table in tables),
        "locators": len(page_doc.get("locators") or []),
        "errors": list(page_doc.get("errors") or []),
    }


def _legacy_record(record: dict, doc, page: int) -> dict:
    from pdf_text import PDFTextSource

    source = PDFTextSource(doc, cache_dir=LEGACY_CACHE_DIR)
    started = time.perf_counter()
    prepared = source.prepare_page(page - 1)
    elapsed = time.perf_counter() - started
    record["legacy_s"] = round(elapsed, 2)
    record["legacy_chars"] = len(prepared.get("text") or "")
    record["legacy_tables"] = len(prepared.get("tables") or [])
    return record


def _cell(record: dict, key: str, missing: str = "-") -> str:
    value = record.get(key)
    return missing if value is None else str(value)


def _print_table(pages: list[dict]) -> None:
    print(
        "| page | docling_s | legacy_s | docling_chars | legacy_chars "
        "| docling_tables | legacy_tables |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for record in pages:
        print(
            f"| {record['page']} | {_cell(record, 'docling_s')} "
            f"| {_cell(record, 'legacy_s')} "
            f"| {_cell(record, 'docling_chars')} "
            f"| {_cell(record, 'legacy_chars')} "
            f"| {_cell(record, 'docling_tables')} "
            f"| {_cell(record, 'legacy_tables')} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True, help="Original PDF path")
    parser.add_argument(
        "--pages",
        required=True,
        help="One-based pages, comma separated with ranges: 64,67-69,78-89",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Also extract the same pages through PDFTextSource",
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf
    pdf_bytes = pdf_path.read_bytes()
    numbers = _parse_pages(args.pages)

    pages: list[dict] = []
    if args.legacy:
        import pymupdf

        doc = pymupdf.open(pdf_path)
        try:
            for page in numbers:
                record = _docling_record(pdf_bytes, pdf_path.name, page)
                pages.append(_legacy_record(record, doc, page))
        finally:
            doc.close()
    else:
        for page in numbers:
            pages.append(_docling_record(pdf_bytes, pdf_path.name, page))

    _print_table(pages)
    print(json.dumps({"pdf": str(pdf_path), "pages": pages}, ensure_ascii=False))


if __name__ == "__main__":
    main()
