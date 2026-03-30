"""Unit tests for PhageScope HTTP + JSON business ``code`` alignment."""

from __future__ import annotations

from tool_box.tools_impl.phagescope import (
    _merge_http_and_business_success,
    _response_with_business_layer,
)


def test_merge_http_ok_without_code_follows_http() -> None:
    ok, meta = _merge_http_and_business_success(200, {"status": "Success", "results": []})
    assert ok is True
    assert meta == {}


def test_merge_http_200_code_0_ok() -> None:
    ok, meta = _merge_http_and_business_success(200, {"code": 0, "message": "ok"})
    assert ok is True
    assert meta.get("business_code") == 0


def test_merge_http_200_code_1_warning_still_ok() -> None:
    ok, meta = _merge_http_and_business_success(200, {"code": 1, "message": "partial"})
    assert ok is True
    assert meta.get("business_warning") is True
    assert meta.get("business_code") == 1


def test_merge_http_200_code_2_fails() -> None:
    ok, meta = _merge_http_and_business_success(
        200, {"code": 2, "message": "Invalid Phage ID", "status": "Error"}
    )
    assert ok is False
    assert meta.get("business_failure") is True
    assert meta.get("business_code") == 2
    assert "Invalid" in (meta.get("error") or "")


def test_response_with_business_layer_includes_error_on_code_2() -> None:
    payload = {"code": 2, "message": "Missing module", "status": "Error"}
    r = _response_with_business_layer("input_check", 200, payload)
    assert r["success"] is False
    assert r["status_code"] == 200
    assert r.get("error")


def test_response_extra_fields_preserved() -> None:
    r = _response_with_business_layer(
        "submit",
        200,
        {"code": 0, "results": {"taskid": 1}},
        endpoint="/analyze/pipline/",
        analysistype="Annotation Pipline",
    )
    assert r["success"] is True
    assert r["endpoint"] == "/analyze/pipline/"
    assert r["analysistype"] == "Annotation Pipline"
