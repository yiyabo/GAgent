from __future__ import annotations

import asyncio
import json

import pytest
import httpx

from tool_box.tools_impl import phagescope as phagescope_module
from tool_box.tools_impl.phagescope import phagescope_handler


def test_extract_taskid_from_submit_result_reads_results_object() -> None:
    """Remote JSON often places taskid under ``results`` (not ``data.data``)."""
    sub = {
        "success": True,
        "status_code": 200,
        "data": {"status": "Success", "results": {"taskid": 38625}},
    }
    tid = phagescope_module._extract_taskid_from_submit_result(sub)
    assert tid == "38625"


def test_phagescope_save_all_rejects_non_numeric_taskid_alias() -> None:
    result = asyncio.run(
        phagescope_handler(
            action="save_all",
            taskid="act_a1c0d8007a554d9a98d688d7394f5ecd",
        )
    )

    assert result["success"] is False
    assert result["status_code"] == 400
    assert result["action"] == "save_all"
    assert result["error_code"] == "invalid_taskid"


def test_phagescope_taskid_alias_resolves_via_action_run_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        phagescope_module,
        "_lookup_remote_taskid_by_tracking_job",
        lambda _job_id, session_id=None: None,
    )
    monkeypatch.setattr(
        phagescope_module,
        "_lookup_remote_taskid_by_action_run",
        lambda _run_id, session_id=None: "37468",
    )

    resolved = phagescope_module._resolve_phagescope_taskid(
        "act_a1c0d8007a554d9a98d688d7394f5ecd",
        session_id="session-x",
    )
    assert resolved == "37468"


def test_phagescope_submit_normalizes_proteins_module_to_annotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def _fake_request(method, base_url, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["data"] = kwargs.get("data")
        return 200, {"status": "Success", "data": {"taskid": 37819}}

    monkeypatch.setattr(phagescope_module, "_request", _fake_request)

    result = asyncio.run(
        phagescope_handler(
            action="submit",
            userid="tester",
            phageid="NC_001416.1",
            analysistype="Annotation Pipline",
            modulelist={"proteins": True, "quality": True},
        )
    )

    assert result["success"] is True
    assert result["requested_modules"] == ["proteins", "quality"]
    assert result["normalized_modules"] == ["annotation", "quality"]
    assert any("normalized to 'annotation'" in item for item in (result.get("warnings") or []))
    posted = captured["data"]
    assert isinstance(posted, dict)
    assert json.loads(str(posted["modulelist"])) == {"annotation": True, "quality": True}


def test_phagescope_submit_rejects_result_only_modulelist_without_submit_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unexpected_request(*_args, **_kwargs):
        raise AssertionError("_request should not be called for invalid submit modulelist")

    monkeypatch.setattr(phagescope_module, "_request", _unexpected_request)

    result = asyncio.run(
        phagescope_handler(
            action="submit",
            userid="tester",
            phageid="NC_001416.1",
            analysistype="Annotation Pipline",
            modulelist={"phagefasta": True},
        )
    )

    assert result["success"] is False
    assert result["status_code"] == 400
    assert "valid submit modules" in str(result["error"]).lower()
    assert result["requested_modules"] == ["phagefasta"]
    assert result.get("normalized_modules") in (None, [])


def test_phagescope_request_retries_on_tls_certificate_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def _fake_do_httpx_request(method, url, **kwargs):
        calls.append(bool(kwargs.get("verify", True)))
        if kwargs.get("verify", True):
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=httpx.Request(method, url))
        return httpx.Response(200, json={"status": "Success"})

    monkeypatch.setattr(phagescope_module, "_do_httpx_request", _fake_do_httpx_request)

    status_code, payload = asyncio.run(
        phagescope_module._request("GET", "https://phageapi.deepomics.org", "/tasks/list/")
    )

    assert status_code == 200
    assert calls == [True, False]
    assert payload["status"] == "Success"
    assert phagescope_module._TLS_RETRY_WARNING in payload["_transport_warnings"]
