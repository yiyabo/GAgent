from pathlib import Path
from typing import Any
import asyncio
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




def test_qwen_early_exit_accepts_flat_canonical_output_view(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch_dir = tmp_path / "scratch"
    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir()
    _ = (canonical_dir / "out.txt").write_text("ok", encoding="utf-8")

    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        if (task_work_dir / "results" / "out.txt").is_file():
            return _Finalization(
                "completed",
                verification={"status": "passed", "checks_total": 1, "checks_passed": 1, "failures": []},
                metadata={"verification_status": "passed"},
            )
        return _Finalization(
            "failed",
            verification={"status": "failed", "checks_total": 1, "checks_passed": 0, "failures": [{"type": "file_exists"}]},
            metadata={"verification_status": "failed"},
        )

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "results/out.txt"}]}},
        task_work_dir=scratch_dir,
        alternate_work_dirs=[canonical_dir],
    )

    assert passed is True
    assert reason == "verification_passed:alternate_output_view"
    assert (scratch_dir / ".contract_views").is_dir()
    assert (scratch_dir / "results" / "out.txt").read_text(encoding="utf-8") == "ok"

def test_qwen_early_exit_accepts_alternate_output_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch_dir = tmp_path / "scratch"
    canonical_dir = tmp_path / "canonical"
    canonical_results = canonical_dir / "results"
    canonical_results.mkdir(parents=True)
    _ = (canonical_results / "out.txt").write_text("ok", encoding="utf-8")

    def _fake_verify(_execution_spec: dict[str, object], *, task_work_dir: Path) -> _Finalization:
        if task_work_dir == canonical_dir:
            return _Finalization(
                "completed",
                verification={"status": "passed", "checks_total": 1, "checks_passed": 1, "failures": []},
                metadata={"verification_status": "passed"},
            )
        return _Finalization(
            "failed",
            verification={"status": "failed", "checks_total": 1, "checks_passed": 0, "failures": [{"type": "file_exists"}]},
            metadata={"verification_status": "failed"},
        )

    monkeypatch.setattr(
        code_executor_module,
        "_verify_contract_for_qwen_early_exit",
        _fake_verify,
    )

    passed, reason = code_executor_module._qwen_outputs_pass_contract_for_early_exit(
        execution_spec={"task_id": 1, "acceptance_criteria": {"checks": [{"type": "file_exists", "path": "results/out.txt"}]}},
        task_work_dir=scratch_dir,
        alternate_work_dirs=[canonical_dir],
    )

    assert passed is True
    assert reason == "verification_passed:alternate_output_dir"
    assert (scratch_dir / "results" / "out.txt").read_text(encoding="utf-8") == "ok"


def test_materializes_unique_run_prefixed_contract_output(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    scratch_dir = tmp_path / "scratch"
    source_artifacts = source_dir / "artifacts"
    source_artifacts.mkdir(parents=True)
    scratch_dir.mkdir()
    prefixed = source_artifacts / "run_20260520_083138_195503_e7cacdd7_dl_hyperopt_results.json"
    prefixed.write_text('{"best_val_auprc": 0.184011}', encoding="utf-8")

    materialized = code_executor_module._materialize_contract_outputs_for_standard_paths(
        execution_spec={
            "acceptance_criteria": {
                "checks": [{"type": "file_nonempty", "path": "artifacts/dl_hyperopt_results.json"}],
            }
        },
        source_dir=source_dir,
        scratch_dir=scratch_dir,
    )

    expected = scratch_dir / "artifacts" / "dl_hyperopt_results.json"
    assert materialized == [expected.resolve()]
    assert expected.read_text(encoding="utf-8") == prefixed.read_text(encoding="utf-8")


def test_does_not_materialize_ambiguous_run_prefixed_contract_outputs(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    scratch_dir = tmp_path / "scratch"
    source_artifacts = source_dir / "artifacts"
    source_artifacts.mkdir(parents=True)
    scratch_dir.mkdir()
    (source_artifacts / "run_a_dl_hyperopt_results.json").write_text("{}", encoding="utf-8")
    (source_artifacts / "run_b_dl_hyperopt_results.json").write_text("{}", encoding="utf-8")

    materialized = code_executor_module._materialize_contract_outputs_for_standard_paths(
        execution_spec={
            "acceptance_criteria": {
                "checks": [{"type": "file_nonempty", "path": "artifacts/dl_hyperopt_results.json"}],
            }
        },
        source_dir=source_dir,
        scratch_dir=scratch_dir,
    )

    assert materialized == []
    assert not (scratch_dir / "artifacts" / "dl_hyperopt_results.json").exists()


def test_missing_required_outputs_accepts_unique_run_prefixed_contract_file(tmp_path: Path) -> None:
    execution_spec = {
        "acceptance_criteria": {
            "category": "file_data",
            "blocking": True,
            "checks": [{"type": "file_nonempty", "path": "artifacts/dl_hyperopt_results.json"}],
        }
    }
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    prefixed = artifacts / "run_20260520_083138_195503_e7cacdd7_dl_hyperopt_results.json"
    prefixed.write_text('{"best_val_auprc": 0.184011}', encoding="utf-8")

    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout="Completed successfully",
        output_data=None,
        execution_spec=execution_spec,
        produced_files=[str(prefixed)],
        success=True,
        task_work_dir=tmp_path,
    )

    assert failure is None


def test_contract_required_artifact_records_use_unique_run_prefixed_contract_file(tmp_path: Path) -> None:
    execution_spec = {
        "acceptance_criteria": {
            "checks": [{"type": "file_nonempty", "path": "artifacts/dl_hyperopt_results.json"}],
        }
    }
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    prefixed = artifacts / "run_20260520_083138_195503_e7cacdd7_dl_hyperopt_results.json"
    prefixed.write_text('{"best_val_auprc": 0.184011}', encoding="utf-8")

    records = code_executor_module._contract_required_artifact_records(
        execution_spec=execution_spec,
        task_work_dir=tmp_path,
        produced_files=[str(prefixed)],
    )

    assert records == [
        {
            "expected": "artifacts/dl_hyperopt_results.json",
            "path": str(prefixed.resolve()),
            "size": prefixed.stat().st_size,
            "exists": True,
            "relative_to_task": "artifacts/run_20260520_083138_195503_e7cacdd7_dl_hyperopt_results.json",
            "verification_source": "contract_required_output",
        }
    ]

class _SlowProcess:
    returncode = None

    def __init__(self) -> None:
        self.kill_called = False
        self._waits = 0

    async def wait(self) -> int:
        self._waits += 1
        if self._waits == 1:
            await asyncio.sleep(1)
        self.returncode = -9
        return -9

    def kill(self) -> None:
        self.kill_called = True


@pytest.mark.asyncio
async def test_cli_process_wait_timeout_kills_stuck_process() -> None:
    process = _SlowProcess()

    return_code = await code_executor_module._wait_for_cli_process_return_code(
        process,
        backend_label="Qwen Code",
        exit_timeout=0.01,
        kill_timeout=0.5,
    )

    assert process.kill_called is True
    assert return_code == -9


class _ExitedProcess:
    returncode = 0

    async def wait(self) -> int:
        raise AssertionError("wait should not be called when returncode is already available")

    def kill(self) -> None:
        raise AssertionError("kill should not be called when returncode is already available")


@pytest.mark.asyncio
async def test_cli_process_wait_uses_existing_return_code() -> None:
    return_code = await code_executor_module._wait_for_cli_process_return_code(
        _ExitedProcess(),
        backend_label="Qwen Code",
        exit_timeout=0.01,
        kill_timeout=0.01,
    )

    assert return_code == 0

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


def test_code_executor_detects_blocked_dependency_status_in_stdout() -> None:
    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout=(
            "The input file is missing.\n"
            "STATUS: BLOCKED_DEPENDENCY\n"
            "DETAIL: /data/input.xlsx does not exist\n"
        ),
        output_data=None,
        execution_spec=None,
        produced_files=[],
        success=True,
    )

    assert failure == {
        "status": "BLOCKED_DEPENDENCY",
        "detail": "/data/input.xlsx does not exist",
        "failure_kind": "blocked_dependency",
    }


def test_code_executor_detects_blocked_dependency_status_in_qwen_events() -> None:
    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout="[]",
        output_data=[
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "STATUS: BLOCKED_DEPENDENCY\nDETAIL: upstream table missing",
                        }
                    ]
                },
            }
        ],
        execution_spec=None,
        produced_files=[],
        success=True,
    )

    assert failure == {
        "status": "BLOCKED_DEPENDENCY",
        "detail": "upstream table missing",
        "failure_kind": "blocked_dependency",
    }


def test_code_executor_ignores_prompt_status_examples_when_qwen_events_exist() -> None:
    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout=(
            "Task prompt said: If blocked, output exactly:\n"
            "STATUS: BLOCKED_DEPENDENCY\n"
            "DETAIL: <which upstream task/data is missing>\n"
        ),
        output_data=[
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Completed successfully."}]
                },
            }
        ],
        execution_spec=None,
        produced_files=["/tmp/result.txt"],
        success=True,
    )

    assert failure is None


def test_code_executor_detects_missing_required_outputs() -> None:
    execution_spec = {
        "acceptance_criteria": {
            "category": "file_data",
            "blocking": True,
            "checks": [{"type": "file_nonempty", "path": "results/report.md"}],
        }
    }

    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout="Completed successfully",
        output_data=None,
        execution_spec=execution_spec,
        produced_files=[],
        success=True,
    )

    assert failure is not None
    assert failure["status"] == "NO_OUTPUT"
    assert failure["failure_kind"] == "missing_required_outputs"
    assert "results/report.md" in failure["detail"]


def test_code_executor_rejects_unrelated_produced_file_for_required_output(tmp_path: Path) -> None:
    execution_spec = {
        "acceptance_criteria": {
            "category": "file_data",
            "blocking": True,
            "checks": [{"type": "file_nonempty", "path": "results/report.md"}],
        }
    }
    log_file = tmp_path / "executor.log"
    log_file.write_text("execution log\n")

    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout="Completed successfully",
        output_data=None,
        execution_spec=execution_spec,
        produced_files=[str(log_file)],
        success=True,
        task_work_dir=tmp_path,
    )

    assert failure is not None
    assert failure["status"] == "NO_OUTPUT"
    assert failure["failure_kind"] == "missing_required_outputs"
    assert "results/report.md" in failure["detail"]


def test_code_executor_accepts_required_output_found_in_task_work_dir(tmp_path: Path) -> None:
    execution_spec = {
        "acceptance_criteria": {
            "category": "file_data",
            "blocking": True,
            "checks": [{"type": "file_nonempty", "path": "results/report.md"}],
        }
    }
    report = tmp_path / "results" / "report.md"
    report.parent.mkdir()
    report.write_text("# report\n")

    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout="Completed successfully",
        output_data=None,
        execution_spec=execution_spec,
        produced_files=[str(tmp_path / "executor.log")],
        success=True,
        task_work_dir=tmp_path,
    )

    assert failure is None


def test_code_executor_ignores_prompt_status_examples_without_qwen_events() -> None:
    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout=(
            "If blocked, output exactly:\n"
            "STATUS: BLOCKED_DEPENDENCY\n"
            "DETAIL: <which upstream task/data is missing>\n"
        ),
        output_data=None,
        execution_spec=None,
        produced_files=[],
        success=True,
    )

    assert failure is None


def test_code_executor_allows_no_outputs_without_output_contract() -> None:
    failure = code_executor_module._detect_execution_semantic_or_output_failure(
        stdout="Completed successfully",
        output_data=None,
        execution_spec=None,
        produced_files=[],
        success=True,
    )

    assert failure is None


def test_code_executor_failure_payload_overrides_exit_zero_success() -> None:
    payload: dict[str, Any] = {
        "success": True,
        "execution_status": "completed",
        "exit_code": 0,
        "produced_files": ["results/report.md"],
        "produced_files_count": 1,
        "artifact_paths": ["results/report.md"],
        "contract_artifacts": [{"path": "results/report.md"}],
        "session_artifact_paths": ["/session/results/report.md"],
        "output_location": {"files": ["/unified/results/report.md"]},
        "deliverable_submit": {"path": "results/report.md"},
    }

    code_executor_module._apply_execution_failure_to_payload(
        payload,
        {
            "status": "BLOCKED_DEPENDENCY",
            "detail": "input.xlsx missing",
            "failure_kind": "blocked_dependency",
        },
    )

    assert payload["success"] is False
    assert payload["execution_status"] == "failed"
    assert payload["failure_kind"] == "blocked_dependency"
    assert payload["error_category"] == "blocked_dependency"
    assert payload["error"] == "BLOCKED_DEPENDENCY: input.xlsx missing"
    assert payload["produced_files"] == []
    assert payload["produced_files_count"] == 0
    assert payload["artifact_paths"] == []
    assert payload["contract_artifacts"] == []
    assert payload["session_artifact_paths"] == []
    assert payload["output_location"]["files"] == []
    assert "deliverable_submit" not in payload
