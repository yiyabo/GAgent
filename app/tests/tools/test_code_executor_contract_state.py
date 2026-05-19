from pathlib import Path
import pytest

from tool_box.tools_impl import code_executor as code_executor_module


class _Finalization:
    final_status: str

    def __init__(
        self,
        final_status: str,
        *,
        verification: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.final_status = final_status
        self.verification = verification
        self.payload = {"metadata": metadata or {}}


def test_clear_stale_contract_failure_state_when_verification_passes() -> None:
    summary, guidance = code_executor_module._clear_stale_contract_failure_state(
        success=True,
        verification_status="passed",
        contract_error_summary="file_nonempty: /tmp/simple_line_chart.png (File is missing or empty.)",
        contract_fix_guidance="write the file to the expected path",
    )

    assert summary is None
    assert guidance is None


def test_clear_stale_contract_failure_state_preserves_real_failure() -> None:
    summary, guidance = code_executor_module._clear_stale_contract_failure_state(
        success=False,
        verification_status="failed",
        contract_error_summary="file_nonempty: /tmp/simple_line_chart.png (File is missing or empty.)",
        contract_fix_guidance="write the file to the expected path",
    )

    assert summary == "file_nonempty: /tmp/simple_line_chart.png (File is missing or empty.)"
    assert guidance == "write the file to the expected path"


def test_qwen_early_exit_requires_acceptance_criteria(tmp_path: Path) -> None:
    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1},
        task_work_dir=tmp_path,
    )

    assert passed is False
    assert reason == "no_acceptance_criteria"


def test_qwen_early_exit_accepts_contract_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        _ = task_work_dir
        return _Finalization(
            "completed",
            verification={"status": "passed", "checks_total": 1, "checks_passed": 1, "failures": []},
            metadata={"verification_status": "passed"},
        )

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "out.txt"}]}},
        task_work_dir=tmp_path,
    )

    assert passed is True
    assert reason == "verification_passed"


def test_qwen_early_exit_rejects_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        _ = task_work_dir
        return _Finalization("failed")

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "out.txt"}]}},
        task_work_dir=tmp_path,
    )

    assert passed is False
    assert reason == "verification_failed"


def test_qwen_early_exit_rejects_empty_acceptance_checks(tmp_path: Path) -> None:
    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": []}},
        task_work_dir=tmp_path,
    )

    assert passed is False
    assert reason == "no_acceptance_checks"


def test_qwen_early_exit_rejects_completed_without_verification_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        _ = task_work_dir
        return _Finalization("completed")

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "out.txt"}]}},
        task_work_dir=tmp_path,
    )

    assert passed is False
    assert reason == "verification_not_passed"


def test_qwen_early_exit_rejects_skipped_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        _ = task_work_dir
        return _Finalization(
            "completed",
            verification={"status": "skipped", "checks_total": 0, "checks_passed": 0, "failures": []},
            metadata={"verification_status": "skipped"},
        )

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "out.txt"}]}},
        task_work_dir=tmp_path,
    )

    assert passed is False
    assert reason == "skipped"


def test_qwen_early_exit_rejects_llm_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        _ = task_work_dir
        return _Finalization(
            "completed",
            verification={
                "status": "passed",
                "checks_total": 1,
                "checks_passed": 1,
                "failures": [],
                "llm_override": True,
            },
            metadata={"verification_status": "passed"},
        )

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "out.txt"}]}},
        task_work_dir=tmp_path,
    )

    assert passed is False
    assert reason == "verification_llm_override"

def test_code_executor_sets_task_local_cache_dirs(tmp_path: Path) -> None:
    env_map: dict[str, str] = {
        "MPLCONFIGDIR": "/definitely/not/writable",
        "XDG_CACHE_HOME": "",
    }

    code_executor_module._ensure_writable_subprocess_cache_env(env_map, tmp_path)

    assert env_map["MPLCONFIGDIR"] == str(tmp_path / ".cache" / "matplotlib")
    assert env_map["XDG_CACHE_HOME"] == str(tmp_path / ".cache" / "xdg")
    assert env_map["NUMBA_CACHE_DIR"] == str(tmp_path / ".cache" / "numba")
    assert (tmp_path / ".cache" / "matplotlib").is_dir()
    assert (tmp_path / ".cache" / "xdg").is_dir()
    assert (tmp_path / ".cache" / "numba").is_dir()
