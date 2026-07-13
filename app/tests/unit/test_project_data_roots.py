from __future__ import annotations

import asyncio
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


def test_project_file_selection_fails_without_platform_file_api(monkeypatch) -> None:
    async def _fake_context(request, project_id):
        return {"id": project_id, "data_roots": []}

    monkeypatch.setattr(project_routes, "_project_context_for_request", _fake_context)
    payload = project_routes.SelectedFilesRequest(
        project_id=9,
        selected_paths=["private.tsv"],
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(project_routes.select_project_files(9, _platform_request(), payload))

    assert exc.value.status_code == 501
