import asyncio
from pathlib import Path

from tool_box.tools_impl import file_operations


def test_write_allows_symlinked_project_data_path(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    external_data = tmp_path / "external_data"
    project_root.mkdir(parents=True, exist_ok=True)
    external_data.mkdir(parents=True, exist_ok=True)

    data_link = project_root / "data"
    data_link.symlink_to(external_data, target_is_directory=True)
    target_file = data_link / "example.txt"

    monkeypatch.setattr(
        file_operations,
        "ALLOWED_BASE_PATHS",
        [str(project_root), str(data_link)],
    )

    result = asyncio.run(
        file_operations.file_operations_handler(
            "write",
            str(target_file),
            content="hello from symlink path",
        )
    )

    assert result["success"] is True
    assert (external_data / "example.txt").read_text(encoding="utf-8") == "hello from symlink path"


def test_validate_rejects_dangerous_absolute_path(monkeypatch) -> None:
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", ["/tmp"])
    is_safe, message = file_operations._validate_path_security("/etc/hosts")
    assert is_safe is False
    assert "allowed directories" in message.lower() or "not allowed" in message.lower()


def test_file_operations_accepts_session_id(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    target_file = project_root / "session-aware.txt"

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(
        file_operations.file_operations_handler(
            "write",
            str(target_file),
            content="session-compatible",
            session_id="session_demo_123",
        )
    )

    assert result["success"] is True
    assert target_file.read_text(encoding="utf-8") == "session-compatible"


def test_copy_allows_large_binary_artifacts_in_allowed_workspace(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    source = project_root / "large_artifact.npz"
    destination = project_root / "copied" / "large_artifact.npz"
    with source.open("wb") as fh:
        fh.truncate(51 * 1024 * 1024)

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(
        file_operations.file_operations_handler(
            "copy",
            str(source),
            destination=str(destination),
        )
    )

    assert result["success"] is True
    assert destination.exists()
    assert destination.stat().st_size == source.stat().st_size


def test_validate_read_like_size_limit_still_blocks_large_files(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    source = project_root / "large.txt"
    source.write_bytes(b"0" * (51 * 1024 * 1024))

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    is_safe, message = file_operations._validate_path_security(
        str(source),
        enforce_file_size_limit=True,
    )

    assert is_safe is False
    assert "too large" in message.lower()


def test_copy_rejects_files_over_500mb_in_allowed_workspace(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    source = project_root / "too_large_artifact.npz"
    destination = project_root / "copied" / "too_large_artifact.npz"
    with source.open("wb") as fh:
        fh.truncate(501 * 1024 * 1024)

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(
        file_operations.file_operations_handler(
            "copy",
            str(source),
            destination=str(destination),
        )
    )

    assert result["success"] is False
    assert "500mb" in result["error"].lower()
    assert not destination.exists()
