"""PhageScope batch_submit / batch_reconcile / batch_retry manifest orchestration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tool_box.tools_impl import phagescope as ph


def test_normalize_phage_id_list_string_and_list() -> None:
    assert ph._normalize_phage_id_list("A;B; C") == ["A", "B", "C"]
    assert ph._normalize_phage_id_list(["NC_1.1", "NC_2.2"]) == ["NC_1.1", "NC_2.2"]


def test_phage_accession_ids_from_result_payload() -> None:
    payload = {
        "results": [
            {"Acession_ID": "X1", "id": 1},
            {"Accession_ID": "X2", "id": 2},
        ]
    }
    ids = ph._phage_accession_ids_from_result_payload(payload)
    assert ids == {"X1", "X2"}


def test_batch_submit_accepts_phageids_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """phagescope_handler must map submit-style phageids into batch_submit (not only phage_ids)."""

    captured: dict = {}

    async def fake_batch_submit(**kwargs: object) -> dict:
        captured["phage_ids"] = kwargs.get("phage_ids")
        return {"success": True}

    monkeypatch.setattr(ph, "_phagescope_batch_submit", fake_batch_submit)

    r = asyncio.run(
        ph.phagescope_handler(
            action="batch_submit",
            phage_ids=None,
            phage_ids_file=None,
            phageids="NC_1.1;NC_2.2",
            modulelist=["quality"],
        )
    )
    assert r["success"] is True
    assert captured["phage_ids"] == "NC_1.1;NC_2.2"


def test_batch_submit_defaults_modulelist_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    async def fake_handler(**kwargs: object) -> dict:
        if kwargs.get("action") == "submit":
            captured["modulelist"] = kwargs.get("modulelist")
            return {
                "success": True,
                "status_code": 200,
                "data": {"status": "Success", "results": {"taskid": 50001}},
            }
        raise AssertionError(kwargs.get("action"))

    monkeypatch.setattr(ph, "phagescope_handler", fake_handler)
    monkeypatch.setattr(ph, "_get_manifests_directory", lambda _sid: (tmp_path / "manifests", None))

    r = asyncio.run(
        ph._phagescope_batch_submit(
            base_url="https://example.invalid",
            token=None,
            timeout=30.0,
            session_id="sess1",
            userid="agent_default_user",
            modulelist=None,
            rundemo="false",
            analysistype="Annotation Pipline",
            inputtype="enter",
            sequence=None,
            file_path=None,
            comparedatabase=None,
            neednum=None,
            phage_ids=["NC_1.1"],
            phage_ids_file=None,
            batch_id="batch-default-mod",
            strategy="multi_one_task",
            manifest_path_override=None,
        )
    )
    assert captured["modulelist"] == ["quality"]
    assert r["primary_taskid"] == "50001"


def test_batch_submit_multi_one_task_writes_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_handler(**kwargs: object) -> dict:
        if kwargs.get("action") == "submit":
            return {
                "success": True,
                "status_code": 200,
                "data": {"status": "Success", "data": {"taskid": 424242}},
            }
        raise AssertionError(f"unexpected action {kwargs.get('action')}")

    monkeypatch.setattr(ph, "phagescope_handler", fake_handler)
    monkeypatch.setattr(ph, "_get_manifests_directory", lambda _sid: (tmp_path / "manifests", None))

    r = asyncio.run(
        ph._phagescope_batch_submit(
            base_url="https://example.invalid",
            token=None,
            timeout=30.0,
            session_id="sess1",
            userid="agent_default_user",
            modulelist=["quality"],
            rundemo="false",
            analysistype="Annotation Pipline",
            inputtype="enter",
            sequence=None,
            file_path=None,
            comparedatabase=None,
            neednum=None,
            phage_ids=["NC_1.1", "NC_2.2"],
            phage_ids_file=None,
            batch_id="batch-unit-test",
            strategy="multi_one_task",
            manifest_path_override=None,
        )
    )
    assert r["success"] is True
    assert r["primary_taskid"] == "424242"
    mfile = tmp_path / "manifests" / "batch-unit-test.json"
    assert mfile.is_file()
    data = json.loads(mfile.read_text(encoding="utf-8"))
    assert data["batch_id"] == "batch-unit-test"
    assert data["requested_phage_ids"] == ["NC_1.1", "NC_2.2"]
    assert data["primary_taskid"] == "424242"


def test_batch_reconcile_computes_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "version": 1,
        "batch_id": "b1",
        "requested_phage_ids": ["A", "B", "C"],
        "primary_taskid": "100",
        "userid": "u",
        "modulelist": ["quality"],
        "rundemo": "false",
    }
    mdir = tmp_path / "manifests"
    mdir.mkdir(parents=True)
    mpath = mdir / "b1.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    async def fake_handler(**kwargs: object) -> dict:
        act = kwargs.get("action")
        if act == "task_detail":
            return {
                "success": True,
                "status_code": 200,
                "data": {"results": {"status": "Success"}},
            }
        if act == "result":
            return {
                "success": True,
                "status_code": 200,
                "data": {"results": [{"Acession_ID": "A", "id": 1}, {"Acession_ID": "B", "id": 2}]},
            }
        raise AssertionError(f"unexpected {act}")

    monkeypatch.setattr(ph, "phagescope_handler", fake_handler)
    monkeypatch.setattr(ph, "_get_manifests_directory", lambda _sid: (mdir, None))

    r = asyncio.run(
        ph._phagescope_batch_reconcile(
            base_url="https://example.invalid",
            token=None,
            timeout=30.0,
            session_id="sess",
            batch_id="b1",
            taskid=None,
            wait=False,
            poll_interval=1.0,
            poll_timeout=30.0,
            manifest_path_override=None,
        )
    )
    assert r["success"] is True
    assert r["missing_phage_ids"] == ["C"]
    updated = json.loads(mpath.read_text(encoding="utf-8"))
    assert updated["last_reconcile"]["missing_phage_ids"] == ["C"]


def test_batch_retry_appends_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {
        "version": 1,
        "batch_id": "b2",
        "requested_phage_ids": ["A", "B"],
        "userid": "agent_default_user",
        "modulelist": ["quality"],
        "rundemo": "false",
        "analysistype": "Annotation Pipline",
        "inputtype": "enter",
        "last_reconcile": {"missing_phage_ids": ["Z"]},
    }
    mdir = tmp_path / "manifests"
    mdir.mkdir(parents=True)
    mpath = mdir / "b2.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    calls: list = []

    async def fake_handler(**kwargs: object) -> dict:
        calls.append(kwargs.get("action"))
        if kwargs.get("action") == "submit":
            return {"success": True, "status_code": 200, "data": {"data": {"taskid": 7777}}}
        raise AssertionError

    monkeypatch.setattr(ph, "phagescope_handler", fake_handler)
    monkeypatch.setattr(ph, "_get_manifests_directory", lambda _sid: (mdir, None))

    r = asyncio.run(
        ph._phagescope_batch_retry(
            base_url="https://example.invalid",
            token=None,
            timeout=30.0,
            session_id="sess",
            userid="agent_default_user",
            modulelist=None,
            rundemo="false",
            analysistype="Annotation Pipline",
            inputtype="enter",
            sequence=None,
            file_path=None,
            comparedatabase=None,
            neednum=None,
            batch_id="b2",
            retry_phage_ids=None,
            manifest_path_override=None,
        )
    )
    assert r["success"] is True
    assert r["retry_phage_ids"] == ["Z"]
    assert calls == ["submit"]
    updated = json.loads(mpath.read_text(encoding="utf-8"))
    assert len(updated["retries"]) == 1
    assert updated["retries"][0]["phage_id"] == "Z"
    assert updated["retries"][0]["taskid"] == "7777"
