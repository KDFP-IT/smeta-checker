"""Contract tests for the docling-serve HTTP client; no live service."""

from __future__ import annotations

import httpx
import pytest

import docling_client as dc


_ORIGINAL_CLIENT = httpx.Client


def _factory(transport: httpx.BaseTransport):
    def factory(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("transport", None)
        return _ORIGINAL_CLIENT(transport=transport, **kwargs)

    return factory


def _run(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(dc.httpx, "Client", _factory(transport))
    monkeypatch.setattr(dc, "RETRY_DELAYS", (0.0, 0.0))


def _success(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/v1/convert/file/async"):
        assert request.method == "POST"
        return httpx.Response(200, json={"task_id": "t1"})
    if request.url.path.endswith("/v1/status/poll/t1"):
        return httpx.Response(200, json={"task_status": "success"})
    if request.url.path.endswith("/v1/result/t1"):
        return httpx.Response(
            200, json={"document": {"json_content": {"pages": {"1": {}}}}}
        )
    raise AssertionError(f"unexpected {request.method} {request.url}")


def test_convert_pages_returns_document_json(monkeypatch):
    _run(monkeypatch, _success)
    result = dc.convert_pages(b"%PDF", filename="a.pdf", page_range=(1, 8))
    assert result == {"pages": {"1": {}}}


def test_failed_task_raises(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/v1/convert/file/async"):
            return httpx.Response(200, json={"task_id": "t1"})
        if request.url.path.endswith("/v1/status/poll/t1"):
            return httpx.Response(200, json={"task_status": "failure", "error": "boom"})
        return httpx.Response(200, json={})

    _run(monkeypatch, handler)
    with pytest.raises(dc.DoclingError, match="failed"):
        dc.convert_pages(b"%PDF", filename="a.pdf", page_range=(1, 1))


def test_422_is_not_retried(monkeypatch):
    calls = {"post": 0}

    def handler(request):
        if request.url.path.endswith("/v1/convert/file/async"):
            calls["post"] += 1
            return httpx.Response(422, text="bad page_range")
        return httpx.Response(200, json={})

    _run(monkeypatch, handler)
    with pytest.raises(dc.DoclingError, match="page_range"):
        dc.convert_pages(b"%PDF", filename="a.pdf", page_range=(1, 1))
    assert calls["post"] == 1


def test_503_retries_then_raises(monkeypatch):
    calls = {"post": 0}

    def handler(request):
        if request.url.path.endswith("/v1/convert/file/async"):
            calls["post"] += 1
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={})

    _run(monkeypatch, handler)
    with pytest.raises(dc.DoclingError, match="unavailable"):
        dc.convert_pages(b"%PDF", filename="a.pdf", page_range=(1, 1))
    assert calls["post"] == 3


def test_empty_json_content_raises(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/v1/convert/file/async"):
            return httpx.Response(200, json={"task_id": "t1"})
        if request.url.path.endswith("/v1/status/poll/t1"):
            return httpx.Response(200, json={"task_status": "success"})
        return httpx.Response(200, json={"document": {"json_content": {}}})

    _run(monkeypatch, handler)
    with pytest.raises(dc.DoclingError, match="empty document"):
        dc.convert_pages(b"%PDF", filename="a.pdf", page_range=(1, 1))
