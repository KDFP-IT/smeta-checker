"""Contract tests for the DoclingDocument -> prepared page adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docling_source import docling_pages

FIXTURE = Path(__file__).parent / "fixtures" / "docling_page.json"


def _document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_page_text_tables_and_locators():
    raw = _document()
    pages = docling_pages(raw)
    assert len(pages) == 1
    page = pages[0]
    assert page["page"] == 1
    assert "Ведомость объёмов работ" in page["text"]
    assert "Раздел 1. Земляные работы" in page["text"]
    assert page["table_rows"] == ["A | B", "1 | 2"]
    assert len(page["locators"]) == 2
    assert [item["source_id"] for item in page["locators"]] == [
        "docling:1:0",
        "docling:1:1",
    ]
    assert page["geometry"]["width"] == 595.0
    assert page["geometry"]["height"] == 842.0
    assert page["geometry"]["matrix"] == [1, 0, 0, 1, 0, 0]
    assert page["errors"] == []
    assert page["table_error"] == ""
    assert page["quality"]["state"] == "usable"
    assert page["quality"]["origin"] == "docling"
    assert page["quality"]["docling"] == {
        "version": raw["version"],
        "name": raw["name"],
        "parse_score": None,
    }
    assert len(page["tables"]) == 1
    table = page["tables"][0]
    assert table["table_id"] == "docling-#/tables/0"
    assert table["page"] == 1
    assert len(table["cells"]) == 4
    assert table["row_count"] == 2
    assert table["col_count"] == 2
    assert table["header_rows"] == 1
    assert table["origin"] == "docling"
    assert [cell["role"] for cell in table["cells"]] == [
        "header",
        "header",
        "body",
        "body",
    ]
    assert table["cells"][0]["cell_id"] == "docling-#/tables/0:cell0-0"
    assert table["cells"][2]["cell_id"] == "docling-#/tables/0:cell1-0"


def test_bottom_left_boxes_become_top_left_points():
    page = docling_pages(_document())[0]
    assert page["tables"][0]["bbox"] == [60.0, 72.0, 520.0, 372.0]
    assert page["locators"][0]["bbox"] == [60.0, 22.0, 400.0, 52.0]


def test_body_children_define_reading_order():
    raw = _document()
    raw["body"]["children"] = [
        {"cref": "#/texts/1"},
        {"cref": "#/tables/0"},
        {"cref": "#/texts/0"},
    ]
    text = docling_pages(raw)[0]["text"]
    assert text.split("\n") == [
        "A | B",
        "1 | 2",
        "Раздел 1. Земляные работы",
        "Ведомость объёмов работ",
    ]


def test_empty_document_has_no_pages():
    assert docling_pages({}) == []


def test_serialized_document_is_rejected_not_read_as_empty():
    with pytest.raises(TypeError):
        docling_pages(json.dumps(_document()))  # type: ignore[arg-type]


def test_page_without_size_is_an_explicit_error():
    raw = _document()
    raw["pages"]["1"] = {"page_no": 1}
    page = docling_pages(raw)[0]
    assert page["errors"] == ["docling_page_geometry_missing"]
    assert page["geometry"]["width"] == 0.0
    assert page["geometry"]["height"] == 0.0
    assert page["text"]  # text survives missing page geometry


def test_table_without_page_is_skipped():
    raw = _document()
    raw["tables"][0]["prov"] = []
    page = docling_pages(raw)[0]
    assert page["tables"] == []
    assert page["table_rows"] == []


def test_pages_fit_the_prepared_document_contract():
    from document_preparation import assemble_document

    pages = docling_pages(_document())
    index = assemble_document(
        {
            "texts": [page["text"] for page in pages],
            "pdf_sha256": "0" * 64,
            "extraction_key": "docling-test",
            "ocr_settings_key": "docling:rapidocr:cyrillic:fast",
            "document_version_id": "v1",
        },
        pages,
    )
    assert index.texts == (pages[0]["text"],)
    assert index.tables == (tuple(pages[0]["tables"]),)
    assert index.table_rows == (("A | B", "1 | 2"),)
    assert index.metadata["page_geometry"]["0"]["width"] == 595.0
    assert index.errors == {}
    assert index.to_snapshot()["tables"] == [pages[0]["tables"]]


def test_formula_original_text_is_used_when_text_is_empty():
    raw = _document()
    raw["texts"][0]["text"] = ""
    raw["texts"][0]["orig"] = "∆= L2 / 2 * R; 0,51 ≈ 0,6"
    page = docling_pages(raw)[0]
    assert "∆= L2 / 2 * R" in page["text"]


def test_position_sort_overrides_body_order():
    raw = _document()
    raw["body"]["children"] = [
        {"cref": "#/texts/1"},
        {"cref": "#/tables/0"},
        {"cref": "#/texts/0"},
    ]
    text = docling_pages(raw, sort_by_position=True)[0]["text"]
    assert text.split("\n") == [
        "A | B",
        "1 | 2",
        "Ведомость объёмов работ",
        "Раздел 1. Земляные работы",
    ]


def test_row_number_and_colspan_are_visible():
    raw = _document()
    cells = raw["tables"][0]["data"]["table_cells"]
    cells[1]["end_col_offset_idx"] = 3
    cells[2]["text"] = "п.п 3"
    page = docling_pages(raw)[0]
    table = page["tables"][0]
    assert table["cells"][2]["text"] == "3"
    assert "B [colspan=2]" in page["table_rows"][0]


def test_wrapped_cell_is_joined_with_next_row():
    raw = _document()
    table = raw["tables"][0]
    table["data"]["num_rows"] = 4
    cells = table["data"]["table_cells"]
    cells.append(
        {
            "text": "щебеночно-",
            "start_row_offset_idx": 2,
            "end_row_offset_idx": 3,
            "start_col_offset_idx": 1,
            "end_col_offset_idx": 2,
            "bbox": {},
        }
    )
    cells.append(
        {
            "text": "мастичный асфальтобетон",
            "start_row_offset_idx": 3,
            "end_row_offset_idx": 4,
            "start_col_offset_idx": 1,
            "end_col_offset_idx": 2,
            "bbox": {},
        }
    )
    page = docling_pages(raw)[0]
    table = page["tables"][0]
    assert any(
        cell["text"] == "щебеночномастичный асфальтобетон" for cell in table["cells"]
    )
    assert all(cell["text"] != "мастичный асфальтобетон" for cell in table["cells"])
