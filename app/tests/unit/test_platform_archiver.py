from __future__ import annotations

import json
from pathlib import Path

from app.services.deliverables import platform_archiver


def test_archive_dirname_is_stable_and_prevents_title_collisions() -> None:
    first = platform_archiver._archive_dirname("session-a", "Same title")
    second = platform_archiver._archive_dirname("session-b", "Same title")

    assert first.startswith("Same title-")
    assert first != second
    assert platform_archiver._archive_dirname("session-a", "Same title") == first


def test_archive_dirname_sanitizes_title() -> None:
    result = platform_archiver._archive_dirname("session-a", "../Unsafe/Title")

    assert "/" not in result
    assert ".." not in result


def test_archive_writes_metadata_after_sync(tmp_path: Path, monkeypatch) -> None:
    session_dir = tmp_path / "runtime" / "session-a"
    raw_file = session_dir / "raw_files" / "result.txt"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_text("result", encoding="utf-8")
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setattr(
        platform_archiver,
        "_lookup_session_project",
        lambda session_id: (7, "owner-a", "Same title"),
    )
    monkeypatch.setattr(platform_archiver, "get_main_platform_user_id", lambda owner_id: 17)
    monkeypatch.setattr(
        platform_archiver,
        "get_project_context",
        lambda user_id, project_id: {"data_roots": [{"path": str(project_root)}]},
    )
    monkeypatch.setattr(platform_archiver, "get_runtime_session_dir", lambda session_id: session_dir)
    monkeypatch.setattr(
        platform_archiver,
        "_write_meta_json",
        lambda session_id, target_dir, project_id: (target_dir / ".meta.json").write_text(
            json.dumps({"session_id": session_id, "project_id": project_id}),
            encoding="utf-8",
        ),
    )

    assert platform_archiver.archive_session_to_platform("session-a") is True

    archive_dirs = list((project_root / "agent_results").iterdir())
    assert len(archive_dirs) == 1
    assert (archive_dirs[0] / "raw_files" / "result.txt").read_text(encoding="utf-8") == "result"
    assert json.loads((archive_dirs[0] / ".meta.json").read_text(encoding="utf-8")) == {
        "session_id": "session-a",
        "project_id": 7,
    }
