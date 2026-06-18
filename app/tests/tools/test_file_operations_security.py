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


def test_default_allowed_base_paths_include_mnt_sdm_zczhao() -> None:
    assert "/mnt/sdm/zczhao" in file_operations.ALLOWED_BASE_PATHS


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


# ---------------------------------------------------------------------------
# Session isolation sandbox regression tests
#
# These tests pin every attack vector that has been patched across multiple
# audit rounds. They guard against regressions in _enforce_session_scope and
# the dangerous-paths global guard. The fixture below rebuilds a minimal
# project tree under tmp_path and redirects APP_RUNTIME_ROOT so that
# get_runtime_root() / get_runtime_session_dir() resolve inside the sandbox.
# ---------------------------------------------------------------------------

import os

from tool_box.context import ToolContext


def _setup_session_sandbox(tmp_path: Path, monkeypatch):
    """Build a fake project root + two sessions and redirect runtime root into it."""
    project_root = tmp_path / "project"
    runtime_root = project_root / "runtime"
    output_root = project_root / "output"
    results_root = project_root / "results"
    for d in (runtime_root, output_root, results_root):
        d.mkdir(parents=True, exist_ok=True)

    # Redirect session_paths so all session dirs resolve under the sandbox.
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    from app.services.session_paths import get_runtime_session_dir

    own = get_runtime_session_dir("own_session", create=True)
    (own / "results").mkdir(parents=True, exist_ok=True)
    (own / "raw_files" / "task_1").mkdir(parents=True, exist_ok=True)
    own_file = own / "results" / "own.csv"
    own_file.write_text("mine", encoding="utf-8")

    other = get_runtime_session_dir("intruder_session", create=True)
    (other / "results").mkdir(parents=True, exist_ok=True)
    victim_file = other / "results" / "victim.csv"
    victim_file.write_text("SECRET_VICTIM", encoding="utf-8")

    global_output_file = output_root / "global.csv"
    global_output_file.write_text("GLOBAL_OUTPUT", encoding="utf-8")

    return {
        "project_root": project_root,
        "own": own,
        "other": other,
        "own_file": own_file,
        "victim_file": victim_file,
        "output_root": output_root,
        "results_root": results_root,
        "global_output_file": global_output_file,
    }


def test_session_read_own_file_is_allowed(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(sandbox["own_file"]), tool_context=ctx)
    )
    assert result["success"] is True
    assert result["content"] == "mine"


def test_session_read_other_session_file_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(sandbox["victim_file"]), tool_context=ctx)
    )
    assert result["success"] is False
    assert "access denied" in result["error"].lower() or "security violation" in result["error"].lower()


def test_session_list_other_session_dir_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("list", str(sandbox["other"]), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_list_operations_now_route_through_security(monkeypatch, tmp_path: Path) -> None:
    """Regression: _list_directory previously skipped _validate_path_security entirely."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    # Listing the global output dir must be blocked under session isolation.
    result = asyncio.run(
        file_operations.file_operations_handler("list", str(sandbox["output_root"]), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_exists_operations_now_route_through_security(monkeypatch, tmp_path: Path) -> None:
    """Regression: _check_exists previously skipped _validate_path_security entirely."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("exists", str(sandbox["victim_file"]), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_info_operations_now_route_through_security(monkeypatch, tmp_path: Path) -> None:
    """Regression: _get_file_info previously skipped _validate_path_security entirely."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("info", str(sandbox["victim_file"]), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_read_global_output_dir_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(sandbox["global_output_file"]), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_read_global_results_dir_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    secret = sandbox["results_root"] / "secret.csv"
    secret.write_text("x", encoding="utf-8")
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(secret), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_list_runtime_root_is_blocked(monkeypatch, tmp_path: Path) -> None:
    """Regression for the original cross-contamination vector: listing project runtime root."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("list", str(sandbox["project_root"] / "runtime"), tool_context=ctx)
    )
    assert result["success"] is False


def test_work_dir_fallback_to_project_root_cannot_bypass(monkeypatch, tmp_path: Path) -> None:
    """Regression: plan_executor's work_dir=os.getcwd() fallback must not smuggle output/ access."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    # work_dir deliberately points at the project root (the fallback scenario).
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["project_root"]))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(sandbox["global_output_file"]), tool_context=ctx)
    )
    assert result["success"] is False


def test_task_work_dir_write_is_allowed(monkeypatch, tmp_path: Path) -> None:
    """Legitimate task output dirs that live inside the session dir must remain writable."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    task_dir = sandbox["own"] / "raw_files" / "task_1"
    ctx = ToolContext(session_id="own_session", work_dir=str(task_dir))
    result = asyncio.run(
        file_operations.file_operations_handler(
            "write", str(task_dir / "out.md"), content="ok", tool_context=ctx
        )
    )
    assert result["success"] is True


def test_session_symlink_to_other_session_is_blocked(monkeypatch, tmp_path: Path) -> None:
    """Regression: a symlink inside session_dir pointing to another session must not bypass."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    link = sandbox["own"] / "escape_link.csv"
    try:
        os.remove(link)
    except FileNotFoundError:
        pass
    os.symlink(sandbox["victim_file"], link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(link), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_symlink_dir_to_other_session_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    dir_link = sandbox["own"] / "escape_dir"
    try:
        os.remove(dir_link)
    except FileNotFoundError:
        pass
    os.symlink(sandbox["other"], dir_link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(dir_link / "results" / "victim.csv"), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_symlink_to_global_output_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    link = sandbox["own"] / "output_link.csv"
    try:
        os.remove(link)
    except FileNotFoundError:
        pass
    os.symlink(sandbox["global_output_file"], link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(link), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_symlink_to_etc_is_blocked(monkeypatch, tmp_path: Path) -> None:
    """Regression: dangerous-paths guard must run BEFORE allowed-base-paths check so a
    symlink whose lexical form is inside session_dir (an allowed dir) cannot read /etc."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    etc_link = sandbox["own"] / "etc_link"
    try:
        os.remove(etc_link)
    except FileNotFoundError:
        pass
    os.symlink("/etc", etc_link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(etc_link / "passwd"), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_symlink_to_proc_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    proc_link = sandbox["own"] / "proc_link"
    try:
        os.remove(proc_link)
    except FileNotFoundError:
        pass
    os.symlink("/proc", proc_link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(proc_link / "self"), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_symlink_to_root_is_blocked(monkeypatch, tmp_path: Path) -> None:
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    root_link = sandbox["own"] / "root_link"
    try:
        os.remove(root_link)
    except FileNotFoundError:
        pass
    os.symlink("/root", root_link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(root_link), tool_context=ctx)
    )
    assert result["success"] is False


def test_session_legit_internal_symlink_is_allowed(monkeypatch, tmp_path: Path) -> None:
    """A symlink inside session_dir that resolves to another path inside the SAME session
    must remain usable (does not over-restrict legitimate internal links)."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    real_target = sandbox["own"] / "results" / "real_target.csv"
    real_target.write_text("internal", encoding="utf-8")
    legit_link = sandbox["own"] / "legit_link.csv"
    try:
        os.remove(legit_link)
    except FileNotFoundError:
        pass
    os.symlink(real_target, legit_link)
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(legit_link), tool_context=ctx)
    )
    assert result["success"] is True
    assert result["content"] == "internal"


def test_session_project_source_is_still_readable(monkeypatch, tmp_path: Path) -> None:
    """Project source dirs (app/, tool_box/) must remain readable under session isolation."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    src = sandbox["project_root"] / "app" / "main.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("print('hi')", encoding="utf-8")
    ctx = ToolContext(session_id="own_session", work_dir=str(sandbox["own"] / "raw_files" / "task_1"))
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(src), tool_context=ctx)
    )
    assert result["success"] is True


def test_session_no_tool_context_falls_back_to_whitelist(monkeypatch, tmp_path: Path) -> None:
    """Backward compatibility: without tool_context, the legacy whitelist still governs."""
    sandbox = _setup_session_sandbox(tmp_path, monkeypatch)
    # A file under project_root (allowed) without any session context must still pass.
    plain = sandbox["project_root"] / "plain.txt"
    plain.write_text("ok", encoding="utf-8")
    result = asyncio.run(
        file_operations.file_operations_handler("read", str(plain))
    )
    assert result["success"] is True


# ---------------------------------------------------------------------------
# Regression: session-scoped reserved directory pollution
#
# Incident (plan_141, session_1781701589917_ho8i76cqo):
#   DeepThink called file_operations.copy with destination "{session}/raw_files"
#   (no trailing filename). shutil.copy2 created a regular FILE at that path
#   instead of a directory, which broke PathRouter and all 16 plan tasks with
#   ENOTDIR. These tests pin the two-layer defence:
#     1. session_paths pre-creates raw_files/uploads/deliverables/tool_outputs
#     2. file_operations _copy_path / _move_path redirect a file copy/move
#        whose destination matches a reserved directory name INTO the directory.
# ---------------------------------------------------------------------------


def test_session_init_pre_creates_reserved_dirs(monkeypatch, tmp_path: Path) -> None:
    """get_runtime_session_dir(create=True) must pre-create reserved dirs
    so a misbehaving copy/move lands inside them instead of replacing them."""
    from app.services import session_paths

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))

    session_dir = session_paths.get_runtime_session_dir("regression_001", create=True)

    assert session_dir.is_dir()
    assert (session_dir / "raw_files").is_dir()
    assert (session_dir / "uploads").is_dir()
    assert (session_dir / "deliverables").is_dir()
    assert (session_dir / "deliverables" / "latest").is_dir()
    assert (session_dir / "tool_outputs").is_dir()


def test_session_init_tolerates_polluted_reserved_name(monkeypatch, tmp_path: Path) -> None:
    """If a regular file already squats on a reserved name (the incident
    scenario), session init must not crash — it leaves the pollutant in place
    for downstream validators to surface, while still creating the others."""
    from app.services import session_paths

    runtime_root = tmp_path / "runtime"
    polluted = runtime_root / "session_polluted"
    polluted.mkdir(parents=True, exist_ok=True)
    # Simulate the incident: a regular file squatting on raw_files
    (polluted / "raw_files").write_bytes(b"PK\x03\x04 polluted")

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime_root))

    session_dir = session_paths.get_runtime_session_dir("polluted", create=True)

    # Pollutant is NOT cleaned up automatically; downstream must surface it
    assert (polluted / "raw_files").is_file()
    # But the other reserved dirs are still created
    assert (session_dir / "uploads").is_dir()
    assert (session_dir / "deliverables" / "latest").is_dir()
    assert (session_dir / "tool_outputs").is_dir()


def test_copy_to_reserved_dir_name_redirects_into_dir(monkeypatch, tmp_path: Path) -> None:
    """Regression (plan_141 incident): when the LLM passes a destination like
    `{dir}/raw_files` without a trailing filename, the file must land INSIDE
    the reserved directory instead of creating a regular file at the reserved
    name (which would break PathRouter and all downstream plan execution)."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    source = project_root / "data.xlsx"
    source.write_bytes(b"PK\x03\x04 fake xlsx payload")

    target_dir = project_root / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / "raw_files"

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(
        file_operations.file_operations_handler("copy", str(source), destination=str(dest))
    )

    assert result["success"] is True
    assert not (target_dir / "raw_files").is_file(), "guard failed: regular file at reserved name"
    assert (target_dir / "raw_files").is_dir()
    assert (target_dir / "raw_files" / "data.xlsx").is_file()
    assert (target_dir / "raw_files" / "data.xlsx").read_bytes() == b"PK\x03\x04 fake xlsx payload"
    assert result["destination"].endswith("data.xlsx")


def test_move_to_reserved_dir_name_redirects_into_dir(monkeypatch, tmp_path: Path) -> None:
    """Same regression as test_copy_* but for move."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    source = project_root / "input.csv"
    source.write_text("a,b\n1,2", encoding="utf-8")

    target_dir = project_root / "out"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / "uploads"

    monkeypatch.setattr(file_operations, "ALLOWED_BASE_PATHS", [str(project_root)])

    result = asyncio.run(
        file_operations.file_operations_handler("move", str(source), destination=str(dest))
    )

    assert result["success"] is True
    assert not (target_dir / "uploads").is_file()
    assert (target_dir / "uploads" / "input.csv").is_file()
    assert (target_dir / "uploads" / "input.csv").read_text(encoding="utf-8") == "a,b\n1,2"
    assert not source.exists()
