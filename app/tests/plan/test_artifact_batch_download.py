from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Tuple

import pytest
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse

from app.config.deliverable_config import DeliverableSettings
from app.routers import artifact_routes


def _allow_access(*args, **kwargs) -> None:
    return None


def _deny_access(*args, **kwargs) -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="session owner mismatch",
    )


def _missing_session(*args, **kwargs) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Session not found",
    )


async def _call_and_read(handler, *args, **kwargs) -> Tuple[StreamingResponse, bytes]:
    response = await handler(*args, **kwargs)
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
        else:
            chunks.append(chunk)
    return response, b"".join(chunks)


def _setup_session_tree(
    tmp_path: Path,
    monkeypatch,
    *,
    session_id: str = "session_batch",
) -> Path:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    session_dir = runtime_root / session_id

    raw_file = session_dir / "raw_files" / "task_1" / "result.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text("raw content\n", encoding="utf-8")

    deliverable_file = session_dir / "deliverables" / "latest" / "docs" / "summary.md"
    deliverable_file.parent.mkdir(parents=True, exist_ok=True)
    deliverable_file.write_text("deliverable content\n", encoding="utf-8")

    manifest_path = session_dir / "deliverables" / "manifest_latest.json"
    manifest_path.write_text(json.dumps({}), encoding="utf-8")

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)
    monkeypatch.setattr(artifact_routes, "_ensure_session_access", _allow_access)

    return session_dir


def _setup_hidden_session(
    tmp_path: Path,
    monkeypatch,
    *,
    session_id: str = "session_hidden",
) -> Path:
    session_dir = _setup_session_tree(tmp_path, monkeypatch, session_id=session_id)

    hidden_file = session_dir / "tool_outputs" / "secret" / "draft.md"
    hidden_file.parent.mkdir(parents=True, exist_ok=True)
    hidden_file.write_text("secret\n", encoding="utf-8")

    manifest_path = session_dir / "deliverables" / "manifest_latest.json"
    manifest_path.write_text(
        json.dumps({"hidden_artifact_prefixes": ["tool_outputs/secret"]}),
        encoding="utf-8",
    )
    return session_dir


def _setup_versioned_session(
    tmp_path: Path,
    monkeypatch,
    *,
    session_id: str = "session_ver",
) -> Path:
    session_dir = _setup_session_tree(tmp_path, monkeypatch, session_id=session_id)

    version_dir = session_dir / "deliverables" / "history" / "v2"
    version_file = version_dir / "docs" / "old_summary.md"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text("versioned content\n", encoding="utf-8")

    (version_dir / "manifest.json").write_text(
        json.dumps({"version_id": "v2"}),
        encoding="utf-8",
    )
    return session_dir


def test_batch_download_happy_path_mixed_raw_and_deliverables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_session_tree(tmp_path, monkeypatch)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[
            artifact_routes.BatchDownloadFileEntry(
                path="raw_files/task_1/result.md",
                scope="raw",
            ),
            artifact_routes.BatchDownloadFileEntry(
                path="docs/summary.md",
                scope="deliverables",
            ),
        ]
    )

    response, body = asyncio.run(
        _call_and_read(
            artifact_routes.batch_download_session_artifacts,
            "session_batch",
            None,
            request_body,
        )
    )

    assert response.media_type == "application/zip"
    cd = response.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "artifacts-session_batch-" in cd
    assert cd.endswith('.zip"')

    zf = zipfile.ZipFile(io.BytesIO(body))
    names = zf.namelist()
    assert "raw_files/task_1/result.md" in names
    assert any(n.endswith("docs/summary.md") for n in names)
    assert zf.read("raw_files/task_1/result.md") == b"raw content\n"
    deliverable_name = [n for n in names if n.endswith("docs/summary.md")][0]
    assert zf.read(deliverable_name) == b"deliverable content\n"


def test_batch_download_empty_files_list_returns_400(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_session_tree(tmp_path, monkeypatch)
    request_body = artifact_routes.BatchDownloadRequest(files=[])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_batch",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "No files requested"


def test_batch_download_too_many_files_returns_400(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_session_tree(tmp_path, monkeypatch)
    files = [
        artifact_routes.BatchDownloadFileEntry(path=f"f{i}.txt", scope="raw")
        for i in range(501)
    ]
    request_body = artifact_routes.BatchDownloadRequest(files=files)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_batch",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "Too many files (max 500)"


def test_batch_download_path_traversal_returns_403(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_session_tree(tmp_path, monkeypatch)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[
            artifact_routes.BatchDownloadFileEntry(
                path="../../etc/passwd",
                scope="raw",
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_batch",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_batch_download_validates_all_files_upfront(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_session_tree(tmp_path, monkeypatch)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[
            artifact_routes.BatchDownloadFileEntry(
                path="raw_files/task_1/result.md",
                scope="raw",
            ),
            artifact_routes.BatchDownloadFileEntry(
                path="../../etc/passwd",
                scope="raw",
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_batch",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_batch_download_hidden_artifact_returns_403(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_hidden_session(tmp_path, monkeypatch)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[
            artifact_routes.BatchDownloadFileEntry(
                path="tool_outputs/secret/draft.md",
                scope="raw",
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_hidden",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_batch_download_non_owner_returns_403(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(artifact_routes, "_ensure_session_access", _deny_access)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[artifact_routes.BatchDownloadFileEntry(path="x.txt", scope="raw")]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_x",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_batch_download_nonexistent_session_returns_404(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(artifact_routes, "_ensure_session_access", _missing_session)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[artifact_routes.BatchDownloadFileEntry(path="x.txt", scope="raw")]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_missing",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_batch_download_deliverable_with_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_versioned_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        artifact_routes,
        "get_deliverable_settings",
        lambda: DeliverableSettings(single_version_only=False),
    )
    request_body = artifact_routes.BatchDownloadRequest(
        files=[
            artifact_routes.BatchDownloadFileEntry(
                path="docs/old_summary.md",
                scope="deliverables",
                version="v2",
            ),
        ]
    )

    response, body = asyncio.run(
        _call_and_read(
            artifact_routes.batch_download_session_artifacts,
            "session_ver",
            None,
            request_body,
        )
    )

    assert response.media_type == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(body))
    names = zf.namelist()
    assert any(n.endswith("docs/old_summary.md") for n in names)
    version_name = [n for n in names if n.endswith("docs/old_summary.md")][0]
    assert zf.read(version_name) == b"versioned content\n"


def test_batch_download_nonexistent_file_returns_404(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _setup_session_tree(tmp_path, monkeypatch)
    request_body = artifact_routes.BatchDownloadRequest(
        files=[
            artifact_routes.BatchDownloadFileEntry(
                path="raw_files/nonexistent.md",
                scope="raw",
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            artifact_routes.batch_download_session_artifacts(
                "session_batch",
                None,
                request_body,
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
