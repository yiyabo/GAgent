from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tool_box
from app.routers import tool_routes
from app.services import session_paths
from tool_box.context import ToolContext

url_fetch_module = importlib.import_module("tool_box.tools_impl.url_fetch")


def test_url_fetch_writes_session_output_and_artifact_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "session_demo"
    session_root = tmp_path / "runtime" / "session_demo"

    def _fake_runtime_session_dir(_sid: str, *, create: bool = False) -> Path:
        if create:
            session_root.mkdir(parents=True, exist_ok=True)
        return session_root

    monkeypatch.setattr(session_paths, "get_runtime_session_dir", _fake_runtime_session_dir)

    async def _fake_download(
        _client: Any,
        _url: str,
        *,
        temp_path: Path,
        timeout_sec: float,
        max_bytes: int,
        allowed_content_types,
        max_redirects: int = 5,
    ) -> Dict[str, Any]:
        _ = (timeout_sec, max_bytes, allowed_content_types, max_redirects)
        temp_path.write_bytes(b"col1,col2\n1,2\n")
        return {
            "final_url": "https://example.com/data.csv",
            "status_code": 200,
            "content_type": "text/csv",
            "bytes": 14,
            "sha256": "3e8c841eeb2e6c4a1ecb5534e1e66c4167d70c06f1d9a1f3c4c7e3fdcb297fdc",
        }

    monkeypatch.setattr(url_fetch_module, "_download_public_url", _fake_download)

    result = asyncio.run(
        url_fetch_module.url_fetch_handler(
            url="https://example.com/data.csv",
            session_id=session_id,
            allowed_content_types=["text/*"],
        )
    )

    assert result["success"] is True
    assert result["content_type"] == "text/csv"
    assert result["output_file_rel"].startswith("tool_outputs/url_fetch/")
    assert result["session_artifact_paths"] == [result["output_file_rel"]]
    assert result["artifact_paths"] == [result["output_file"]]
    assert result["output_location"]["files"] == [result["output_file_rel"]]
    assert Path(result["output_file"]).exists()


def test_url_fetch_uses_task_work_dir_and_task_output_location(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "session_task"
    session_root = tmp_path / "runtime" / session_id
    task_dir = session_root / "raw_files" / "task_5" / "task_12"

    def _fake_runtime_session_dir(_sid: str, *, create: bool = False) -> Path:
        if create:
            session_root.mkdir(parents=True, exist_ok=True)
        return session_root

    monkeypatch.setattr(session_paths, "get_runtime_session_dir", _fake_runtime_session_dir)

    async def _fake_download(
        _client: Any,
        _url: str,
        *,
        temp_path: Path,
        timeout_sec: float,
        max_bytes: int,
        allowed_content_types,
        max_redirects: int = 5,
    ) -> Dict[str, Any]:
        _ = (timeout_sec, max_bytes, allowed_content_types, max_redirects)
        temp_path.write_text("example\n", encoding="utf-8")
        return {
            "final_url": "https://example.com/report.txt",
            "status_code": 200,
            "content_type": "text/plain",
            "bytes": 8,
            "sha256": "c3499c2729730a7f807efb8676a92dcb6dc43a2d1b2d0f2953b6b1f770d5d4e1",
        }

    monkeypatch.setattr(url_fetch_module, "_download_public_url", _fake_download)

    result = asyncio.run(
        url_fetch_module.url_fetch_handler(
            url="https://example.com/report.txt",
            ancestor_chain=[5],
            tool_context=ToolContext(
                session_id=session_id,
                task_id=12,
                work_dir=str(task_dir),
            ),
        )
    )

    assert result["success"] is True
    assert Path(result["output_file"]).parent == task_dir
    assert result["output_location"]["type"] == "task"
    assert result["output_location"]["task_id"] == 12
    assert result["output_location"]["ancestor_chain"] == [5]
    assert result["output_file_rel"].startswith("raw_files/task_5/task_12/")


def test_url_fetch_rejects_private_host() -> None:
    with pytest.raises(url_fetch_module.UrlFetchError) as exc:
        url_fetch_module._ensure_public_host("http://127.0.0.1/file.txt")

    assert exc.value.code == "non_public_host"
    assert exc.value.stage == "network_request"


def test_url_fetch_reports_sha256_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        url_fetch_module,
        "_resolve_output_dir",
        lambda _sid, *, tool_context=None: output_dir,
    )

    async def _fake_download(
        _client: Any,
        _url: str,
        *,
        temp_path: Path,
        timeout_sec: float,
        max_bytes: int,
        allowed_content_types,
        max_redirects: int = 5,
    ) -> Dict[str, Any]:
        _ = (timeout_sec, max_bytes, allowed_content_types, max_redirects)
        temp_path.write_bytes(b"payload")
        return {
            "final_url": "https://example.com/payload.bin",
            "status_code": 200,
            "content_type": "application/octet-stream",
            "bytes": 7,
            "sha256": "f" * 64,
        }

    monkeypatch.setattr(url_fetch_module, "_download_public_url", _fake_download)

    result = asyncio.run(
        url_fetch_module.url_fetch_handler(
            url="https://example.com/payload.bin",
            sha256="0" * 64,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "sha256_mismatch"
    assert list(output_dir.iterdir()) == []


def test_url_fetch_route_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    async def _fake_execute_tool(name: str, **kwargs: Any) -> Dict[str, Any]:
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "tool": "url_fetch",
            "output_file": "/tmp/example.csv",
            "bytes": 12,
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    app = FastAPI()
    app.include_router(tool_routes.router)
    client = TestClient(app)

    response = client.post(
        "/tools/url-fetch",
        json={
            "url": "https://example.com/data.csv",
            "output_name": "data.csv",
            "session_id": "session_demo",
            "allowed_content_types": ["text/csv"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert captured["name"] == "url_fetch"
    assert captured["kwargs"]["url"] == "https://example.com/data.csv"
    assert captured["kwargs"]["output_name"] == "data.csv"
    assert captured["kwargs"]["allowed_content_types"] == ["text/csv"]
