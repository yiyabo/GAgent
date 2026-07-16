from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import project_routes
from app.services.request_principal import RequestPrincipal


def _platform_request(project_id: int = 9):
    return SimpleNamespace(
        state=SimpleNamespace(
            principal=RequestPrincipal(
                user_id="local-user",
                email="user@example.com",
                auth_source="sso",
                access_mode="platform",
                platform_user_id=17,
                platform_project_id=project_id,
            )
        )
    )


def test_project_context_hides_agent_host_data_root_paths(monkeypatch) -> None:
    async def _fake_context(request, project_id):
        return {
            "id": project_id,
            "data_roots": [{"path": "/agent-host/private", "label": "Study data"}],
        }

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)

    response = asyncio.run(project_routes.get_project(9, _platform_request()))

    assert response.code == 0
    assert response.data is not None
    assert response.data.data_roots[0].path == ""
    assert response.data.data_roots[0].label == "Study data"


def test_project_context_accepts_object_model_options(monkeypatch) -> None:
    async def _fake_context(request, project_id):
        return {
            "id": project_id,
            "data_roots": [],
            "model_provider": {
                "type": "openai",
                "model": "qwen3.7-max",
                "base_url": "https://example.com/v1",
                "model_options": [
                    {"key": "qwen3.7-max", "name": "qwen3.7-max", "description": ""},
                    {"key": "text-embedding-v4", "name": "text-embedding-v4", "description": ""},
                    "plain-model-id",
                ],
            },
        }

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)

    response = asyncio.run(project_routes.get_project(20, _platform_request(20)))

    assert response.code == 0
    assert response.data is not None
    assert response.data.model_provider is not None
    assert response.data.model_provider.model_options == [
        "qwen3.7-max",
        "text-embedding-v4",
        "plain-model-id",
    ]


def test_project_files_lists_shared_data_root(monkeypatch, tmp_path: Path) -> None:
    sample = tmp_path / "notes.txt"
    sample.write_text("hello", encoding="utf-8")
    nested = tmp_path / "subdir"
    nested.mkdir()
    (nested / "inner.csv").write_text("a,b\n", encoding="utf-8")

    async def _fake_context(request, project_id):
        return {
            "id": project_id,
            "data_roots": [{"path": str(tmp_path), "label": "Study data"}],
        }

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)

    response = asyncio.run(project_routes.get_project_files(9, _platform_request()))

    assert response.code == 0
    titles = {node.title for node in response.data}
    assert "notes.txt" in titles
    assert "subdir" in titles
    leaf = next(node for node in response.data if node.title == "notes.txt")
    assert leaf.is_leaf is True
    assert leaf.key == "notes.txt"


def test_project_file_selection_copies_into_session(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "private.tsv"
    source.write_text("x\t1\n", encoding="utf-8")
    session_dir = tmp_path / "session_uploads"
    session_dir.mkdir()

    async def _fake_context(request, project_id):
        return {
            "id": project_id,
            "data_roots": [{"path": str(root), "label": "Study data"}],
        }

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)
    monkeypatch.setattr(
        "app.services.session_paths.get_session_upload_dir",
        lambda session_id, create=False: session_dir,
    )

    payload = project_routes.SelectedFilesRequest(
        project_id=9,
        selected_paths=["private.tsv"],
        session_id="session_test",
    )
    response = asyncio.run(
        project_routes.select_project_files(9, _platform_request(), payload)
    )

    assert response.code == 0
    assert len(response.files) == 1
    assert response.files[0].name == "private.tsv"
    assert Path(response.files[0].path).exists()
    assert Path(response.files[0].path).read_text(encoding="utf-8") == "x\t1\n"
    assert response.files[0].data_root_path == ""


def test_project_file_selection_rejects_path_traversal(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("ok", encoding="utf-8")

    async def _fake_context(request, project_id):
        return {
            "id": project_id,
            "data_roots": [{"path": str(root), "label": "Study data"}],
        }

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)
    payload = project_routes.SelectedFilesRequest(
        project_id=9,
        selected_paths=["../secret.txt"],
    )
    response = asyncio.run(
        project_routes.select_project_files(9, _platform_request(), payload)
    )
    assert response.code == 0
    assert response.files == []


def test_project_file_selection_fails_when_no_data_roots(monkeypatch) -> None:
    async def _fake_context(request, project_id):
        return {"id": project_id, "data_roots": []}

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)
    payload = project_routes.SelectedFilesRequest(
        project_id=9,
        selected_paths=["private.tsv"],
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(project_routes.select_project_files(9, _platform_request(), payload))

    assert exc.value.status_code == 404
