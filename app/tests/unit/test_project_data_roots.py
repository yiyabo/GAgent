from __future__ import annotations

import asyncio
from pathlib import Path

from app.routers import project_routes


def test_select_files_uses_the_requested_data_root(tmp_path: Path, monkeypatch) -> None:
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    (root_a / "shared.txt").parent.mkdir(parents=True)
    root_b.mkdir()
    (root_a / "shared.txt").write_text("from-a", encoding="utf-8")
    (root_b / "shared.txt").write_text("from-b", encoding="utf-8")

    monkeypatch.setattr(project_routes, "_resolve_user_id", lambda request, user_id: 1)
    monkeypatch.setattr(
        project_routes,
        "get_project_context",
        lambda user_id, project_id: {
            "data_roots": [{"path": str(root_a)}, {"path": str(root_b)}]
        },
    )

    payload = project_routes.SelectedFilesRequest(
        project_id=9,
        selected_paths=["shared.txt"],
        data_root_index=1,
    )
    result = asyncio.run(project_routes.select_project_files(9, object(), payload))

    assert result.files[0].data_root_path == str(root_b)
