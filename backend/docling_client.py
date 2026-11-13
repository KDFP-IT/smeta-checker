"""HTTP client for the isolated ``docling-serve`` PDF extraction service.

The service owns layout/OCR/table models; this module only submits a page range
and returns the ``DoclingDocument`` JSON. Failures are explicit: the caller
must record a preparation error instead of silently extracting another corpus.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_ENDPOINT = "http://docling:5001"
DEFAULT_TIMEOUT = 600.0
POLL_INTERVAL_S = 2.0
RETRY_DELAYS = (1.0, 3.0)


class DoclingError(RuntimeError):
    """Explicit preparation failure; never silently falls back."""


def _form_fields(
    page_range: tuple[int, int], timeout: float, batch_options: dict[str, Any] | None
) -> dict[str, Any]:
    start, end = int(page_range[0]), int(page_range[1])
    fields: dict[str, Any] = {
        "from_formats": "pdf",
        "to_formats": "json",
        "page_range": [str(start), str(end)],
        "do_ocr": "true",
        "ocr_preset": "rapidocr",
        "ocr_lang": "cyrillic",
        "table_mode": "fast",
        # TableFormer on full-page scans is 3-5x slower than legacy grid OCR;
        # keep it opt-in until a per-region table path lands.
        "do_table_structure": os.environ.get("DOCLING_SERVE_TABLE_STRUCTURE", "false"),
        "include_images": "false",
        "include_page_images": "false",
        "images_scale": "1.0",
        "do_code_enrichment": "false",
        "do_formula_enrichment": "false",
        "do_picture_description": "false",
        "abort_on_error": "true",
        "document_timeout": str(timeout),
    }
    for key, value in (batch_options or {}).items():
        if key == "request_id":
            continue
        fields[str(key)] = str(value)
    if fields["do_table_structure"] == "true":
        fields["table_structure_preset"] = os.environ.get(
            "DOCLING_SERVE_TABLE_STRUCTURE_PRESET", "kdfp"
        )
    return fields


def _headers(batch_options: dict[str, Any] | None) -> dict[str, str]:
    request_id = str((batch_options or {}).get("request_id") or "")
    return {"X-Docling-Log-RequestID": request_id} if request_id else {}


def _error_body(response: httpx.Response) -> str:
    try:
        text = response.text
    except Exception:
        return ""
    return text[:500]


def _submit(
    client: httpx.Client,
    endpoint: str,
    pdf_bytes: bytes,
    filename: str,
    page_range: tuple[int, int],
    timeout: float,
    batch_options: dict[str, Any] | None,
) -> str:
    files = {"files": (filename, pdf_bytes, "application/pdf")}
    form = _form_fields(page_range, timeout, batch_options)
    response = client.post(
        f"{endpoint}/v1/convert/file/async",
        files=files,
        data=form,
        headers=_headers(batch_options),
    )
    if response.status_code in {400, 422}:
        raise DoclingError(
            f"docling rejected request ({response.status_code}): {_error_body(response)}"
        )
    response.raise_for_status()
    payload = response.json()
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        raise DoclingError("docling returned no task_id")
    return task_id


def _poll(
    client: httpx.Client,
    endpoint: str,
    task_id: str,
    deadline: float,
    batch_options: dict[str, Any] | None,
) -> None:
    url = f"{endpoint}/v1/status/poll/{task_id}"
    while True:
        if time.monotonic() >= deadline:
            raise DoclingError(f"docling task {task_id} timed out")
        response = client.get(url, headers=_headers(batch_options))
        response.raise_for_status()
        task = response.json()
        status = str(task.get("task_status") or "")
        if status == "success":
            return
        if status == "failure":
            detail = task.get("task_meta") or task.get("error") or task
            raise DoclingError(f"docling task {task_id} failed: {str(detail)[:500]}")
        time.sleep(POLL_INTERVAL_S)


def _result(
    client: httpx.Client,
    endpoint: str,
    task_id: str,
    batch_options: dict[str, Any] | None,
) -> dict:
    response = client.get(
        f"{endpoint}/v1/result/{task_id}", headers=_headers(batch_options)
    )
    response.raise_for_status()
    payload = response.json()
    document = payload.get("document") if isinstance(payload, dict) else None
    content = document.get("json_content") if isinstance(document, dict) else None
    if not isinstance(content, dict) or not content:
        raise DoclingError("docling returned empty document")
    return content


def _convert_once(
    endpoint: str,
    pdf_bytes: bytes,
    filename: str,
    page_range: tuple[int, int],
    timeout: float,
    batch_options: dict[str, Any] | None,
) -> dict:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
        task_id = _submit(
            client, endpoint, pdf_bytes, filename, page_range, timeout, batch_options
        )
        _poll(client, endpoint, task_id, deadline, batch_options)
        return _result(client, endpoint, task_id, batch_options)


def convert_pages(
    pdf_bytes: bytes,
    *,
    filename: str,
    page_range: tuple[int, int],
    endpoint: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    batch_options: dict[str, Any] | None = None,
) -> dict:
    """Return DoclingDocument JSON (``document.json_content``) for a page range."""
    endpoint = (
        endpoint or os.environ.get("DOCLING_SERVE_ENDPOINT", DEFAULT_ENDPOINT)
    ).rstrip("/")
    if not pdf_bytes:
        raise DoclingError("empty PDF bytes")
    last_error: Exception | None = None
    for _attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            return _convert_once(
                endpoint, pdf_bytes, filename, page_range, timeout, batch_options
            )
        except DoclingError:
            raise
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_error = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {502, 503, 504}:
                raise DoclingError(
                    f"docling HTTP {exc.response.status_code}: {_error_body(exc.response)}"
                ) from exc
            last_error = exc
        except httpx.HTTPError as exc:
            last_error = exc
    raise DoclingError(f"docling unavailable: {last_error}")
