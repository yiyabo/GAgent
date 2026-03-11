import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException, status

from app.routers import artifact_routes


def test_resolve_session_dir_prefers_expected_root_by_purpose(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    runtime_session = runtime_root / "session_abc123"
    info_session = info_root / "session-session_abc123"

    (runtime_session / "deliverables").mkdir(parents=True, exist_ok=True)
    (runtime_session / "deliverables" / "manifest_latest.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (info_session / "tool_outputs").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)

    raw_dir = artifact_routes._resolve_session_dir("session_abc123", purpose="raw")
    deliverable_dir = artifact_routes._resolve_session_dir(
        "session_abc123", purpose="deliverables"
    )

    assert raw_dir == info_session.resolve()
    assert deliverable_dir == runtime_session.resolve()


def test_resolve_session_dir_handles_repeated_prefixes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    legacy_runtime_session = runtime_root / "session_session_xyz"
    legacy_runtime_session.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)

    resolved = artifact_routes._resolve_session_dir(
        "session-session_xyz", purpose="generic"
    )
    assert resolved == legacy_runtime_session.resolve()


def test_resolve_session_dir_prefers_runtime_for_raw_when_both_have_tool_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    runtime_session = runtime_root / "session_abc123"
    info_session = info_root / "session-session_abc123"

    (runtime_session / "tool_outputs").mkdir(parents=True, exist_ok=True)
    (info_session / "tool_outputs").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)

    resolved = artifact_routes._resolve_session_dir("session_abc123", purpose="raw")
    assert resolved == runtime_session.resolve()


@pytest.mark.parametrize("target", ["list", "manifest"])
def test_session_missing_returns_empty_payload(monkeypatch, target: str) -> None:
    def _raise_not_found(*args, **kwargs):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="missing")

    monkeypatch.setattr(artifact_routes, "_resolve_session_dir", _raise_not_found)

    if target == "list":
        response = asyncio.run(artifact_routes.list_session_deliverables("session_missing"))
        assert response.count == 0
        assert response.items == []
        assert response.modules == {}
    else:
        response = asyncio.run(
            artifact_routes.get_session_deliverables_manifest("session_missing")
        )
        assert response.manifest == {}
        assert response.manifest_path is None


def test_render_cache_hash_changes_when_refs_change(tmp_path: Path) -> None:
    paper_dir = tmp_path / "deliverables" / "latest" / "paper"
    refs_dir = tmp_path / "deliverables" / "latest" / "refs"
    paper_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    main_tex = paper_dir / "main.tex"
    refs_bib = refs_dir / "references.bib"
    main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
    refs_bib.write_text("@article{a,\n  title={First}\n}\n", encoding="utf-8")

    before = artifact_routes._get_render_cache_path(main_tex, "pdf")
    refs_bib.write_text("@article{a,\n  title={Updated reference title}\n}\n", encoding="utf-8")
    os.utime(refs_bib, None)
    after = artifact_routes._get_render_cache_path(main_tex, "pdf")

    assert before != after


def test_render_cache_hash_changes_when_staged_figure_changes(tmp_path: Path) -> None:
    paper_dir = tmp_path / "deliverables" / "latest" / "paper"
    figures_dir = paper_dir / "figures"
    paper_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    main_tex = paper_dir / "main.tex"
    figure = figures_dir / "plot.png"
    main_tex.write_text("\\documentclass{article}\n", encoding="utf-8")
    figure.write_bytes(b"old-plot")

    before = artifact_routes._get_render_cache_path(main_tex, "pdf")
    figure.write_bytes(b"new-plot-content")
    os.utime(figure, None)
    after = artifact_routes._get_render_cache_path(main_tex, "pdf")

    assert before != after


def test_list_session_deliverables_exposes_paper_drafts_when_sections_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    info_root = tmp_path / "information_sessions"
    session_dir = runtime_root / "session_abc123"
    latest_root = session_dir / "deliverables" / "latest"
    paper_file = latest_root / "paper" / "sections" / "abstract.tex"
    refs_file = latest_root / "refs" / "references.bib"
    manifest_path = session_dir / "deliverables" / "manifest_latest.json"

    paper_file.parent.mkdir(parents=True, exist_ok=True)
    refs_file.parent.mkdir(parents=True, exist_ok=True)
    paper_file.write_text("\\begin{abstract}draft abstract\\end{abstract}\n", encoding="utf-8")
    refs_file.write_text("@article{draft,\n  title={Draft}\n}\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "version_id": "v1",
                "items": [
                    {"module": "paper", "path": "paper/sections/abstract.tex", "status": "draft"},
                    {"module": "refs", "path": "refs/references.bib", "status": "draft"},
                ],
                "paper_status": {
                    "completed_sections": ["abstract"],
                    "missing_sections": ["introduction"],
                    "total_sections": 2,
                    "completed_count": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(artifact_routes, "RUNTIME_DIR", runtime_root)
    monkeypatch.setattr(artifact_routes, "INFO_SESSIONS_DIR", info_root)

    response = asyncio.run(
        artifact_routes.list_session_deliverables(
            "session_abc123",
            scope="latest",
            version=None,
            include_draft=False,
            module=None,
            limit=1000,
        )
    )

    assert response.count == 2
    assert sorted(response.modules.keys()) == ["paper", "refs"]
    assert {item.status for item in response.items} == {"draft"}
    assert response.paper_status["completed_count"] == 1
