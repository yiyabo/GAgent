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


def test_profile_directory_surfaces_status_counts_and_failures(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    output_dir = project_root / "humann_output"
    progress_dir = output_dir / "humann_progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(5):
        sample_dir = output_dir / f"sample_{idx}"
        sample_dir.mkdir()
        (sample_dir / "result.tsv").write_text("x\n", encoding="utf-8")
    (output_dir / "sample_failed").mkdir()
    (progress_dir / "completed.txt").write_text(
        "sample_0\nsample_1\nsample_2\nsample_3\nsample_4\nmissing_sample\n",
        encoding="utf-8",
    )
    (progress_dir / "failed.txt").write_text("sample_failed\n", encoding="utf-8")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(file_operations.file_operations_handler("profile", str(output_dir)))

    assert result["success"] is True
    assert result["operation"] == "profile"
    assert result["counts"]["completed"] == 6
    assert result["counts"]["failed"] == 1
    assert result["counts"]["directories"] == 7
    assert result["counts"]["sample_candidate_directories"] == 6
    assert result["counts"]["status_directories"] == 1
    assert result["evidence_scope"]["status_counts"]["completed"] == 6
    assert result["evidence_scope"]["status_counts"]["failed"] == 1
    classification = result["evidence_scope"]["directory_classification"]
    assert classification["sample_candidate_directories"] == 6
    assert classification["status_directories"] == 1
    assert classification["status_directory_names"] == ["humann_progress"]
    reconciliation = result["reconciliation"]
    assert reconciliation["schema"] == "file_operations.reconciliation.v1"
    assert reconciliation["counts"]["sample_candidate_directories"] == 6
    assert reconciliation["counts"]["status_unique_total"] == 7
    assert reconciliation["counts"]["success_missing_directories"] == 1
    assert reconciliation["counts"]["failure_missing_directories"] == 0
    assert reconciliation["examples"]["success_missing_directories"] == ["missing_sample"]
    assert reconciliation["success_directory_structure"]["file_count_distribution"] == {"1": 5}
    assert reconciliation["success_directory_structure"]["complete_scan"] is True
    assert result["status_counts_confidence"] == "high"
    assert result["status_count_sources"][0]["count_source"] == "manifest_like"
    assert any(item["name"] == "failed.txt" for item in result["status_files"])
    guidance = " ".join(result["evidence_scope"]["claim_guidance"])
    assert "Failure status files" in guidance
    assert "Direct child count includes status/progress directories" in guidance
    assert "Status-file total differs" in guidance


def test_list_directory_includes_evidence_scope_status_counts(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    output_dir = project_root / "run_output"
    progress_dir = output_dir / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sample_a").mkdir()
    (progress_dir / "completed.txt").write_text("sample_a\n", encoding="utf-8")
    (progress_dir / "failed.txt").write_text("sample_b\n", encoding="utf-8")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(file_operations.file_operations_handler("list", str(output_dir)))

    assert result["success"] is True
    assert result["evidence_scope"]["completeness_status"] == "complete"
    assert "status_counts" not in result["evidence_scope"]


def test_profile_directory_does_not_count_log_lines_as_failed_items(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    output_dir = project_root / "run_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "error.log").write_text("line one\nline two\n", encoding="utf-8")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(file_operations.file_operations_handler("profile", str(output_dir)))

    assert result["success"] is True
    assert "failed" not in result["counts"]
    assert result["status_counts_confidence"] == "none"
    assert result["status_files"][0]["count_confidence"] == "low"


def test_census_preserves_requested_operation(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    output_dir = project_root / "run_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(file_operations.file_operations_handler("census", str(output_dir)))

    assert result["success"] is True
    assert result["operation"] == "census"
    assert result["evidence_scope"]["operation"] == "census"


def test_profile_file_returns_metadata_instead_of_directory_error(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    source = project_root / "annotated_adata.h5ad"
    source.write_bytes(b"h5ad-placeholder")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(file_operations.file_operations_handler("profile", str(source)))

    assert result["success"] is True
    assert result["operation"] == "profile"
    assert result["type"] == "file"
    assert result["suffix"] == ".h5ad"
    assert result["evidence_scope"]["scope"] == "single_file"
    assert "directory census" in " ".join(result["evidence_scope"]["claim_guidance"])


def test_census_file_preserves_requested_operation(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    source = project_root / "communication_scores.tsv"
    source.write_text("a\tb\n1\t2\n", encoding="utf-8")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(file_operations.file_operations_handler("census", str(source)))

    assert result["success"] is True
    assert result["operation"] == "census"
    assert result["type"] == "file"
    assert result["evidence_scope"]["operation"] == "census"


def test_profile_rejects_disallowed_absolute_directory(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("hidden\n", encoding="utf-8")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(allowed)])

    result = asyncio.run(file_operations.file_operations_handler("profile", str(outside)))

    assert result["success"] is False
    assert "security violation" in result["error"].lower()


def test_census_rejects_disallowed_absolute_file(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    secret = outside / "secret.tsv"
    secret.write_text("a\tb\n1\t2\n", encoding="utf-8")

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(allowed)])

    result = asyncio.run(file_operations.file_operations_handler("census", str(secret)))

    assert result["success"] is False
    assert "security violation" in result["error"].lower()
