"""Adapt ``DoclingDocument`` JSON to the prepared-page contract.

``docling-serve`` owns layout, OCR and table structure; this module only maps
its JSON onto the page dictionaries ``document_preparation.assemble_document``
already consumes. Missing geometry stays an explicit page error instead of a
silently empty page, and bounding boxes are converted from Docling's
bottom-left PDF origin to the corpus top-left PDF points.
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["docling_pages"]


def _number(value: Any) -> float:
    if type(value) in (int, float) and math.isfinite(float(value)):
        return float(value)
    return 0.0


def _offset(value: Any, default: int) -> int:
    return value if type(value) is int and value >= 0 else default


def _bbox(value: Any, height: float) -> list[float]:
    """Docling bbox -> ``[l, t, r, b]`` in top-left PDF points, or ``[]``.

    Docling reports PDF boxes with a bottom-left origin, while every corpus
    consumer (evidence rows, geometry validation) expects top-left points.
    A degenerate box is unusable and must not masquerade as evidence.
    """
    if not isinstance(value, dict):
        return []
    try:
        left, top, right, bottom = (float(value[key]) for key in ("l", "t", "r", "b"))
    except KeyError, TypeError, ValueError:
        return []
    if not all(math.isfinite(item) for item in (left, top, right, bottom)):
        return []
    if "BOTTOMLEFT" in str(value.get("coord_origin") or "").upper() and height > 0:
        top, bottom = height - top, height - bottom
    left, right = min(left, right), max(left, right)
    top, bottom = min(top, bottom), max(top, bottom)
    if left == right or top == bottom:
        return []
    return [left, top, right, bottom]


def _provenance(item: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    """First provenance entry carrying a one-based page number."""
    prov = item.get("prov")
    if not isinstance(prov, list):
        return None
    for entry in prov:
        if not isinstance(entry, dict):
            continue
        page_no = entry.get("page_no")
        if type(page_no) is int and page_no >= 1:
            return page_no, entry
    return None


def _ordered_texts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Text items in reading order, falling back to document order.

    ``body.children`` may address nested groups, so groups are walked
    recursively; items absent from the body are appended afterwards rather
    than dropped, because a silent omission would read as absent evidence.
    """
    texts = [item for item in (raw.get("texts") or []) if isinstance(item, dict)]
    if not texts:
        return []
    by_ref = {
        item["self_ref"]: item
        for item in texts
        if isinstance(item.get("self_ref"), str)
    }
    groups = raw.get("groups")
    children: dict[str, Any] = {}
    for group in [raw.get("body"), *(groups if isinstance(groups, list) else [])]:
        if isinstance(group, dict) and isinstance(group.get("children"), list):
            ref = group.get("self_ref")
            if isinstance(ref, str):
                children[ref] = group["children"]
    order: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(entries: Any, depth: int) -> None:
        if depth > 8 or not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # docling-core 2.x serializes refs as ``cref``; older dumps used
            # ``$ref``/``ref``, so accept every spelling seen in the wild.
            ref = entry.get("cref") or entry.get("$ref") or entry.get("ref")
            if not isinstance(ref, str):
                continue
            if ref in children:
                walk(children[ref], depth + 1)
                continue
            item = by_ref.get(ref)
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            order.append(item)

    body = raw.get("body")
    if isinstance(body, dict):
        walk(body.get("children"), 0)
    order.extend(item for item in texts if id(item) not in seen)
    return order


def _cell(table_id: str, value: dict[str, Any], height: float) -> dict[str, Any]:
    row = _offset(value.get("start_row_offset_idx"), 0)
    column = _offset(value.get("start_col_offset_idx"), 0)
    rowspan = max(1, _offset(value.get("end_row_offset_idx"), row + 1) - row)
    colspan = max(1, _offset(value.get("end_col_offset_idx"), column + 1) - column)
    role = (
        "header"
        if value.get("column_header")
        else ("group" if value.get("row_section") else "body")
    )
    return {
        "cell_id": f"{table_id}:cell{row}-{column}",
        "row": row,
        "column": column,
        "rowspan": rowspan,
        "colspan": colspan,
        "bbox": _bbox(value.get("bbox"), height),
        "text": str(value.get("text") or ""),
        "role": role,
        "quality": "usable",
        "recognition": {"state": "docling", "confidence": None, "readings": []},
        "header_ids": [],
        "header_verification": {},
        "recovery": False,
    }


def _row_strings(cells: list[dict[str, Any]]) -> list[str]:
    """Flatten physical cells into one pipe-joined string per populated row.

    A merged cell is emitted once (at its start row) with an explicit span
    marker so ``table_rows`` does not pretend the grid is rectangular.
    """
    rows: list[str] = []
    for index in sorted({cell["row"] for cell in cells}):
        parts = []
        for cell in sorted(
            (cell for cell in cells if cell["row"] == index),
            key=lambda cell: cell["column"],
        ):
            text = cell["text"].strip()
            if not text:
                continue
            if cell["colspan"] > 1:
                text += f" [colspan={cell['colspan']}]"
            if cell["rowspan"] > 1:
                text += f" [rowspan={cell['rowspan']}]"
            parts.append(text)
        if parts:
            rows.append(" | ".join(parts))
    return rows


def _normalize_row_number(text: str, *, header: bool) -> str:
    """Table 12.x splits ``№ п.п`` into the header and row numbers."""
    if header:
        return text
    match = re.fullmatch(r"(?:№\s*)?п\.?\s*п\.?\s*(\d+)", text, re.IGNORECASE)
    return match.group(1) if match else text


def _merge_wrapped_cells(cells: list[dict[str, Any]]) -> None:
    """Join a hyphen-wrapped cell with its continuation on the next row."""
    by_position = {(cell["row"], cell["column"]): cell for cell in cells}
    for cell in cells:
        text = cell["text"].strip()
        if not text or not text.endswith(("-", "‑", "–")):
            continue
        following = by_position.get((cell["row"] + 1, cell["column"]))
        if following is None or not following["text"].strip():
            continue
        if following["role"] == "header":
            continue
        cell["text"] = text[:-1] + following["text"].strip()
        following["text"] = ""


def _table(
    item: dict[str, Any], page_no: int, provenance: dict[str, Any], height: float
) -> tuple[dict[str, Any], list[str]]:
    table_id = f"docling-{item.get('self_ref')}"
    raw_data = item.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    raw_cells = data.get("table_cells")
    cells = [
        _cell(table_id, value, height)
        for value in (raw_cells if isinstance(raw_cells, list) else [])
        if isinstance(value, dict)
    ]
    for cell in cells:
        cell["text"] = _normalize_row_number(
            cell["text"], header=cell["role"] == "header"
        )
    _merge_wrapped_cells(cells)
    header_cells = [cell for cell in cells if cell["role"] == "header"]
    for cell in cells:
        if cell["role"] != "body":
            continue
        covering = [
            header
            for header in header_cells
            if header["row"] < cell["row"]
            and header["column"] <= cell["column"] < header["column"] + header["colspan"]
        ]
        if covering:
            cell["header_ids"] = [
                header["cell_id"]
                for header in sorted(covering, key=lambda item: (item["row"], item["column"]))
            ]
    row_count = _offset(data.get("num_rows"), 0)
    col_count = _offset(data.get("num_cols"), 0)
    for cell in cells:
        row_count = max(row_count, cell["row"] + cell["rowspan"])
        col_count = max(col_count, cell["column"] + cell["colspan"])
    header_rows = (
        max((cell["row"] for cell in cells if cell["role"] == "header"), default=-1) + 1
    )
    table = {
        "table_id": table_id,
        "page": page_no,
        "bbox": _bbox(provenance.get("bbox"), height),
        "cells": cells,
        "row_count": row_count,
        "col_count": col_count,
        "header_rows": header_rows,
        "origin": "docling",
        "quality": "usable",
        "header_verification": {},
        "recovery": False,
    }
    return table, _row_strings(cells)


def _locator(
    page_no: int, index: int, text: str, provenance: dict[str, Any], height: float
) -> dict[str, Any]:
    return {
        "source_id": f"docling:{page_no}:{index}",
        "document_id": "",
        "page": page_no,
        "text": text,
        "bbox": _bbox(provenance.get("bbox"), height),
        "origin": "docling",
        "table_id": "",
        "row_id": "",
        "column_id": "",
        "header_ids": [],
        "group_id": "",
        "scope_key": "",
        "role": "block",
        "quality": "usable",
        "rotation": 0,
    }


def docling_pages(
    raw: dict[str, Any], *, sort_by_position: bool = False
) -> list[dict[str, Any]]:
    """DoclingDocument JSON -> page dicts compatible with assemble_document.

    ``sort_by_position`` orders text items by page bbox instead of Docling's
    model reading order; engineering PDFs need captions before their tables.
    """
    if not isinstance(raw, dict):
        raise TypeError("docling document must be a JSON object")
    if not raw:
        return []
    sizes: dict[int, dict[str, Any] | None] = {}
    pages_raw = raw.get("pages")
    if isinstance(pages_raw, dict):
        for key, value in pages_raw.items():
            if not isinstance(value, dict):
                continue
            try:
                page_no = int(key)
            except TypeError:
                continue
            except ValueError:
                continue
            if page_no < 1:
                continue
            size = value.get("size")
            # ``None`` marks a page Docling reported without usable geometry.
            sizes[page_no] = size if isinstance(size, dict) and size else None

    texts: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for item in _ordered_texts(raw):
        found = _provenance(item)
        if found is None:
            continue
        texts.setdefault(found[0], []).append((item, found[1]))

    tables: dict[int, list[tuple[dict[str, Any], list[str]]]] = {}
    raw_tables = raw.get("tables")
    for item in raw_tables if isinstance(raw_tables, list) else []:
        if not isinstance(item, dict):
            continue
        provenance = _provenance(item)
        if provenance is None:
            # A table without a physical page cannot be cited; never guess one.
            continue
        page_no, entry = provenance
        height = _number((sizes.get(page_no) or {}).get("height"))
        tables.setdefault(page_no, []).append(_table(item, page_no, entry, height))

    meta = {
        "version": str(raw.get("version") or ""),
        "name": str(raw.get("name") or ""),
        "parse_score": raw.get("confidence") or raw.get("parse_score"),
    }
    prepared: list[dict[str, Any]] = []
    for page_no in sorted(set(sizes) | set(texts) | set(tables)):
        size = sizes.get(page_no)
        errors = [] if size is not None else ["docling_page_geometry_missing"]
        width = _number((size or {}).get("width"))
        height = _number((size or {}).get("height"))
        page_texts = list(texts.get(page_no) or [])
        if sort_by_position and page_texts:
            boxes = [_bbox(entry.get("bbox"), height) for _, entry in page_texts]
            if all(boxes):
                order = sorted(
                    range(len(page_texts)), key=lambda i: (boxes[i][1], boxes[i][0])
                )
                page_texts = [page_texts[i] for i in order]
        parts: list[str] = []
        locators: list[dict[str, Any]] = []
        for index, (item, entry) in enumerate(page_texts):
            text = str(item.get("text") or item.get("orig") or "").strip()
            locators.append(_locator(page_no, index, text, entry, height))
            if text:
                parts.append(text)
        entries = tables.get(page_no) or []
        table_text = "\n".join(row for _, rows in entries for row in rows)
        page_text = "\n".join([table_text, *parts]) if table_text else "\n".join(parts)
        prepared.append(
            {
                "page": page_no,
                "text": page_text,
                "table_rows": [row for _, rows in entries for row in rows],
                "locators": locators,
                "tables": [table for table, _ in entries],
                "quality": {
                    "state": "usable",
                    "origin": "docling",
                    "docling": dict(meta),
                },
                "errors": errors,
                "geometry": {
                    "width": width,
                    "height": height,
                    "matrix": [1, 0, 0, 1, 0, 0],
                },
                "table_error": "",
            }
        )
    return prepared
