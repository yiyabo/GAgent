"""Tests for local code execution backend in TaskExecutor."""

import asyncio
from types import SimpleNamespace
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.interpreter.task_executer import TaskExecutor, TaskType, TaskExecutionResult
from app.services.interpreter.local_interpreter import CodeExecutionResult
from app.services.interpreter.coder import CodeTaskResponse
from app.services.interpreter.code_execution import (
    classify_error,
    CodeExecutionSpec,
    CodeExecutionOutcome,
    _collect_guidance_artifact_paths,
    _format_acceptance_checks,
    _format_verification_guidance,
    _spec_requests_batch_profile,
    execute_code_locally,
)
from app.services.interpreter.prompts.coder_prompt import build_coder_system_prompt

# Module where execute_code_locally's internals live (for monkeypatching).
_CE_MOD = "app.services.interpreter.code_execution"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch: pytest.MonkeyPatch):
    """Clear lru_cache on executor settings to avoid cross-test pollution."""
    from app.config.executor_config import get_executor_settings
    monkeypatch.setenv("CODE_EXECUTOR_LOCAL_RUNTIME", "host")
    get_executor_settings.cache_clear()
    yield
    get_executor_settings.cache_clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_file(tmp_path: Path) -> Path:
    """Create a minimal TSV data file for TaskExecutor."""
    data_file = tmp_path / "test_data.tsv"
    data_file.write_text("col_a\tcol_b\n1\t2\n3\t4\n")
    return data_file


@pytest.fixture
def make_executor(tmp_path: Path, tmp_data_file: Path):
    """Factory to create a TaskExecutor with mocked LLM service."""
    def _make():
        llm = MagicMock()
        llm.chat = MagicMock(return_value="mocked")
        executor = TaskExecutor(
            data_file_paths=[str(tmp_data_file)],
            output_dir=str(tmp_path / "output"),
            llm_service=llm,
        )
        return executor
    return _make


# ---------------------------------------------------------------------------
# classify_error tests
# ---------------------------------------------------------------------------

def test_classify_error_missing_package():
    stderr = "ModuleNotFoundError: No module named 'seaborn'"
    assert classify_error(stderr, 1) == "missing_package"


def test_classify_error_syntax():
    assert classify_error("SyntaxError: invalid syntax", 1) == "syntax_error"


def test_classify_error_timeout():
    assert classify_error("", -1) == "timeout"


def test_classify_error_file_access():
    assert classify_error("FileNotFoundError: [Errno 2]", 1) == "file_access"


def test_classify_error_runtime():
    assert classify_error("ValueError: bad value", 1) == "runtime_error"


def test_classify_error_blocked_dependency():
    stderr = "BLOCKED_DEPENDENCY: fewer than 2 valid samples available. Cannot proceed with integration."
    assert classify_error(stderr, 1) == "blocked_dependency"


def test_classify_error_ignores_leading_warning_noise():
    stderr = (
        "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
        "UserWarning: pkg_resources is deprecated as an API.\n"
        "  from pkg_resources import get_distribution\n"
        "ModuleNotFoundError: No module named 'seaborn'\n"
    )
    assert classify_error(stderr, 1) == "missing_package"


def test_classify_error_warning_only_noise():
    stderr = (
        "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
        "UserWarning: pkg_resources is deprecated as an API.\n"
        "  from pkg_resources import get_distribution\n"
    )
    assert classify_error(stderr, 1) == "non_fatal_warning_noise"


def test_execute_code_locally_skips_auto_fix_for_blocked_dependency(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('hello')", description="blocked")
    warning_only_stderr = (
        "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
        "UserWarning: pkg_resources is deprecated as an API.\n"
        "  from pkg_resources import get_distribution\n"
    )
    failed_exec_result = CodeExecutionResult(
        status="failed",
        output="ERROR: Fewer than 2 valid samples available. Cannot proceed with integration.\n",
        error=warning_only_stderr,
        exit_code=1,
    )

    monkeypatch.setattr(
        f"{_CE_MOD}.CodeGenerator.generate",
        lambda self, **kw: fake_code_resp,
    )
    monkeypatch.setattr(
        f"{_CE_MOD}.LocalCodeInterpreter.run_file",
        lambda self, code_file: failed_exec_result,
    )

    def _unexpected_fix(*args, **kwargs):
        raise AssertionError("auto-fix should not run for blocked dependencies")

    monkeypatch.setattr(f"{_CE_MOD}._ask_llm_to_fix", _unexpected_fix)

    outcome = _run(
        execute_code_locally(
            task_title="integration",
            task_description="attempt integration",
            work_dir=str(tmp_path),
            llm_service=MagicMock(),
            auto_fix=True,
            max_attempts=3,
        )
    )

    assert outcome.success is False
    assert outcome.attempts == 1
    assert outcome.error_category == "blocked_dependency"
    assert "Fewer than 2 valid samples" in (outcome.error_summary or "")


def test_execute_code_locally_verifies_explicit_acceptance_criteria(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('qc done')", description="qc")

    def _mock_run_file(self, code_file):
        results_dir = Path(tmp_path) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "filtered_cancer1.h5ad").write_text("ok", encoding="utf-8")
        return CodeExecutionResult(
            status="success",
            output="qc done\n",
            error="",
            exit_code=0,
        )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", _mock_run_file)

    spec = CodeExecutionSpec(
        plan_id=68,
        task_id=3,
        task_name="QC",
        task_instruction="Generate filtered sample outputs",
        acceptance_criteria={
            "blocking": True,
            "checks": [
                {
                    "type": "glob_count_at_least",
                    "glob": "results/filtered_*.h5ad",
                    "min_count": 2,
                }
            ],
        },
    )

    outcome = _run(
        execute_code_locally(
            task_title="task 3",
            task_description="run qc",
            work_dir=str(tmp_path),
            llm_service=MagicMock(),
            execution_spec=spec,
        )
    )

    assert outcome.success is False
    assert outcome.error_category == "acceptance_criteria_failed"
    assert "expected at least 2" in (outcome.error_summary or "")


def test_execute_code_locally_accepts_single_artifact_contract(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('integration done')", description="integration")

    def _mock_run_file(self, code_file):
        results_dir = Path(tmp_path) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "integrated_data.h5ad").write_text("merged", encoding="utf-8")
        return CodeExecutionResult(
            status="success",
            output="integration done\n",
            error="",
            exit_code=0,
        )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", _mock_run_file)

    spec = CodeExecutionSpec(
        plan_id=68,
        task_id=4,
        task_name="Integration",
        task_instruction="Integrate filtered samples",
        acceptance_criteria={
            "blocking": True,
            "checks": [
                {"type": "file_exists", "path": "results/integrated_data.h5ad"},
                {"type": "file_nonempty", "path": "results/integrated_data.h5ad"},
            ],
        },
    )

    outcome = _run(
        execute_code_locally(
            task_title="task 4",
            task_description="integrate samples",
            work_dir=str(tmp_path),
            llm_service=MagicMock(),
            execution_spec=spec,
        )
    )

    assert outcome.success is True
    assert outcome.error_category is None


def test_execute_code_locally_repairs_contract_mismatch_once(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('initial')", description="markers")
    run_count = {"value": 0}

    def _mock_run_file(self, code_file):
        run_count["value"] += 1
        results_dir = Path(tmp_path) / "results"
        enrichment_dir = results_dir / "enrichment"
        results_dir.mkdir(parents=True, exist_ok=True)
        enrichment_dir.mkdir(parents=True, exist_ok=True)
        wrong_file = results_dir / "NK_cell_upregulated_genes.csv"
        expected_file = enrichment_dir / "upregulated_genes.csv"
        if run_count["value"] == 1:
            wrong_file.write_text("gene,logFC\nA,1.0\n", encoding="utf-8")
            if expected_file.exists():
                expected_file.unlink()
        else:
            expected_file.write_text("gene,logFC\nA,1.0\n", encoding="utf-8")
        return CodeExecutionResult(
            status="success",
            output="markers done\n",
            error="",
            exit_code=0,
        )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", _mock_run_file)
    async def _repair_contract(**kwargs):
        return "print('repaired')"

    monkeypatch.setattr(f"{_CE_MOD}._ask_llm_to_repair_contract", _repair_contract)

    spec = CodeExecutionSpec(
        plan_id=68,
        task_id=34,
        task_name="差异基因提取与分类",
        task_instruction="按 plan 生成聚合后的上调基因文件",
        acceptance_criteria={
            "blocking": True,
            "checks": [
                {"type": "file_exists", "path": "results/enrichment/upregulated_genes.csv"},
                {"type": "file_nonempty", "path": "results/enrichment/upregulated_genes.csv"},
            ],
        },
    )

    outcome = _run(
        execute_code_locally(
            task_title="task 34",
            task_description="extract differential genes",
            work_dir=str(tmp_path),
            llm_service=MagicMock(),
            execution_spec=spec,
            auto_fix=True,
        )
    )

    assert outcome.success is True
    assert outcome.execution_status == "completed"
    assert outcome.verification_status == "passed"
    assert outcome.failure_kind is None
    assert outcome.repair_attempts == 1
    assert outcome.error_category is None


def test_execute_code_locally_fails_after_contract_repair_exhausted(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('initial')", description="markers")

    def _mock_run_file(self, code_file):
        results_dir = Path(tmp_path) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "NK_cell_upregulated_genes.csv").write_text(
            "gene,logFC\nA,1.0\n",
            encoding="utf-8",
        )
        return CodeExecutionResult(
            status="success",
            output="markers done\n",
            error="",
            exit_code=0,
        )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", _mock_run_file)
    async def _repair_contract(**kwargs):
        return "print('repaired but still wrong')"

    monkeypatch.setattr(f"{_CE_MOD}._ask_llm_to_repair_contract", _repair_contract)

    spec = CodeExecutionSpec(
        plan_id=68,
        task_id=34,
        task_name="差异基因提取与分类",
        task_instruction="按 plan 生成聚合后的上调基因文件",
        acceptance_criteria={
            "blocking": True,
            "checks": [
                {"type": "file_exists", "path": "results/enrichment/upregulated_genes.csv"},
            ],
        },
    )

    outcome = _run(
        execute_code_locally(
            task_title="task 34",
            task_description="extract differential genes",
            work_dir=str(tmp_path),
            llm_service=MagicMock(),
            execution_spec=spec,
            auto_fix=True,
        )
    )

    assert outcome.success is False
    assert outcome.execution_status == "completed"
    assert outcome.verification_status == "failed"
    assert outcome.failure_kind == "contract_mismatch"
    assert outcome.error_category == "acceptance_criteria_failed"
    assert outcome.repair_attempts == 1
    assert outcome.contract_diff is not None
    assert outcome.contract_diff["missing_required_outputs"] == [
        "results/enrichment/upregulated_genes.csv"
    ]


def test_collect_guidance_artifact_paths_includes_custom_acceptance_dirs(tmp_path: Path):
    figures_dir = tmp_path / "manuscript" / "figures"
    figures_dir.mkdir(parents=True)
    legends = figures_dir / "Figure_Legends.docx"
    legends.write_text("legend", encoding="utf-8")
    pdf = figures_dir / "figure1.pdf"
    pdf.write_text("pdf", encoding="utf-8")

    collected = _collect_guidance_artifact_paths(
        str(tmp_path),
        acceptance_criteria={
            "checks": [
                {"type": "glob_count_at_least", "path": "manuscript/figures/*.pdf", "count": 1},
                {"type": "file_nonempty", "path": "manuscript/figures/Figure_Legends.docx"},
            ]
        },
    )

    assert str(legends) in collected
    assert str(pdf) in collected


def test_format_verification_guidance_mentions_expected_path_and_same_name_candidate(tmp_path: Path):
    misplaced = tmp_path / "manuscript" / "Figure_Legends.docx"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_text("legend", encoding="utf-8")

    guidance = _format_verification_guidance(
        {
            "failures": [
                {
                    "type": "file_nonempty",
                    "path": str(tmp_path / "manuscript" / "figures" / "Figure_Legends.docx"),
                    "message": "File is missing or empty.",
                }
            ],
            "evidence": {"artifact_paths": [str(misplaced)]},
        }
    )

    assert "Expected path" in guidance
    assert str(tmp_path / "manuscript" / "figures" / "Figure_Legends.docx") in guidance
    assert str(misplaced) in guidance
    assert "Move or copy the valid file to the expected path" in guidance


def test_execute_code_locally_reports_specific_guidance_for_misplaced_required_file(
    tmp_path: Path,
    monkeypatch,
):
    fake_code_resp = CodeTaskResponse(code="print('exported')", description="export")

    def _mock_run_file(self, code_file):
        manuscript_dir = Path(tmp_path) / "manuscript"
        figures_dir = manuscript_dir / "figures"
        manuscript_dir.mkdir(parents=True, exist_ok=True)
        figures_dir.mkdir(parents=True, exist_ok=True)
        (manuscript_dir / "Figure_Legends.docx").write_text("legend", encoding="utf-8")
        for index in range(10):
            (figures_dir / f"figure_{index}.tiff").write_text("tiff", encoding="utf-8")
            (figures_dir / f"figure_{index}.pdf").write_text("pdf", encoding="utf-8")
        return CodeExecutionResult(
            status="success",
            output="export done\n",
            error="",
            exit_code=0,
        )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", _mock_run_file)

    spec = CodeExecutionSpec(
        plan_id=68,
        task_id=65,
        task_name="高分辨率导出与质量验证",
        task_instruction="导出高分辨率图表并生成图例文档",
        acceptance_criteria={
            "blocking": True,
            "checks": [
                {"type": "glob_count_at_least", "path": "manuscript/figures/*.tiff", "count": 10},
                {"type": "glob_count_at_least", "path": "manuscript/figures/*.pdf", "count": 10},
                {"type": "file_nonempty", "path": "manuscript/figures/Figure_Legends.docx"},
            ],
        },
    )

    outcome = _run(
        execute_code_locally(
            task_title="task 65",
            task_description="export publication figures",
            work_dir=str(tmp_path),
            llm_service=MagicMock(),
            execution_spec=spec,
            auto_fix=False,
        )
    )

    assert outcome.success is False
    assert outcome.error_category == "acceptance_criteria_failed"
    assert "manuscript/figures/Figure_Legends.docx" in (outcome.error_summary or "")
    assert "manuscript/Figure_Legends.docx" in (outcome.fix_guidance or "")
    assert "Move or copy the valid file to the expected path" in (outcome.fix_guidance or "")


def test_format_acceptance_checks_supports_legacy_glob_count_shape():
    formatted = _format_acceptance_checks({
        "checks": [
            {
                "type": "glob_count_at_least",
                "path": "output/4.1.2/significant_interactions.csv",
                "count": 2,
            }
        ]
    })

    assert formatted == [
        "at least 2 matches for glob: output/4.1.2/significant_interactions.csv"
    ]


def test_spec_requests_batch_profile_supports_legacy_glob_count_shape():
    spec = CodeExecutionSpec(
        acceptance_criteria={
            "checks": [
                {
                    "type": "glob_count_at_least",
                    "path": "output/batch/*.csv",
                    "count": 3,
                }
            ]
        }
    )

    assert _spec_requests_batch_profile(spec) is True


# ---------------------------------------------------------------------------
# Local execution: success path (via unified execute_code_locally)
# ---------------------------------------------------------------------------

def test_local_execution_success(make_executor, monkeypatch):
    """LLM generates code → writes to file → subprocess runs it → success."""
    executor = make_executor()

    fake_code_resp = CodeTaskResponse(
        code="print('hello')",
        description="prints hello",
    )
    fake_exec_result = CodeExecutionResult(
        status="success", output="hello\n", error="", exit_code=0,
    )

    monkeypatch.setattr(
        f"{_CE_MOD}.CodeGenerator.generate",
        lambda self, **kw: fake_code_resp,
    )
    monkeypatch.setattr(
        f"{_CE_MOD}.LocalCodeInterpreter.run_file",
        lambda self, code_file: fake_exec_result,
    )

    result = _run(executor._execute_code_task_local(
        task_title="test", task_description="print hello",
    ))

    assert result.success is True
    assert result.task_type == TaskType.CODE_REQUIRED
    assert result.final_code == "print('hello')"
    assert result.code_output == "hello\n"
    assert result.total_attempts == 1
    assert result.error_message is None


# ---------------------------------------------------------------------------
# Local execution: retry then success
# ---------------------------------------------------------------------------

def test_local_execution_retry_then_success(make_executor, monkeypatch):
    """First attempt fails → LLM fixes code → second attempt succeeds."""
    executor = make_executor()

    fake_code_resp = CodeTaskResponse(code="bad()", description="bad code")
    fixed_code_resp = CodeTaskResponse(code="print('fixed')", description="fixed")

    call_count = {"exec": 0}

    def mock_run_file(self, code_file):
        call_count["exec"] += 1
        if call_count["exec"] == 1:
            return CodeExecutionResult(
                status="failed", output="", error="NameError: bad", exit_code=1,
            )
        return CodeExecutionResult(
            status="success", output="fixed\n", error="", exit_code=0,
        )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", lambda self, **kw: fixed_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", mock_run_file)

    result = _run(executor._execute_code_task_local(
        task_title="test", task_description="fix me",
    ))

    assert result.success is True
    assert result.total_attempts == 2
    assert result.final_code == "print('fixed')"


# ---------------------------------------------------------------------------
# Local execution: all retries fail
# ---------------------------------------------------------------------------

def test_local_execution_all_retries_fail(make_executor, monkeypatch):
    """All 3 attempts fail → returns error with error_category."""
    executor = make_executor()

    fake_code_resp = CodeTaskResponse(code="bad()", description="bad")

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(
        f"{_CE_MOD}.CodeGenerator.fix_code",
        lambda self, **kw: CodeTaskResponse(code="still_bad()", description="still bad"),
    )
    monkeypatch.setattr(
        f"{_CE_MOD}.LocalCodeInterpreter.run_file",
        lambda self, code_file: CodeExecutionResult(
            status="failed", output="", error="SyntaxError", exit_code=1,
        ),
    )

    result = _run(executor._execute_code_task_local(
        task_title="test", task_description="always fails",
    ))

    assert result.success is False
    assert result.total_attempts == 3
    assert result.error_message is not None
    assert "SyntaxError" in result.error_message


# ---------------------------------------------------------------------------
# Backend switch: legacy CLI path
# ---------------------------------------------------------------------------

def test_backend_switch_to_code_executor(make_executor, monkeypatch):
    """When CODE_EXECUTION_BACKEND=claude_code, delegates to legacy CLI method."""
    executor = make_executor()

    monkeypatch.setattr(
        "app.services.interpreter.task_executer.get_executor_settings",
        lambda: MagicMock(code_execution_backend="claude_code"),
    )

    legacy_called = {"value": False}

    async def mock_legacy(self, *a, **kw):
        legacy_called["value"] = True
        return TaskExecutionResult(
            task_type=TaskType.CODE_REQUIRED, success=True,
        )

    monkeypatch.setattr(
        TaskExecutor, "_execute_code_task_legacy_cli", mock_legacy,
    )

    result = _run(executor._execute_code_task(
        task_title="test", task_description="use legacy cli",
    ))

    assert legacy_called["value"] is True
    assert result.success is True


def test_backend_switch_to_qwen_code(make_executor, monkeypatch):
    """When CODE_EXECUTION_BACKEND=qwen_code, delegates to legacy CLI method (shared path)."""
    executor = make_executor()

    monkeypatch.setattr(
        "app.services.interpreter.task_executer.get_executor_settings",
        lambda: MagicMock(code_execution_backend="qwen_code"),
    )

    legacy_called = {"value": False}

    async def mock_legacy(self, *a, **kw):
        legacy_called["value"] = True
        return TaskExecutionResult(
            task_type=TaskType.CODE_REQUIRED, success=True,
        )

    monkeypatch.setattr(
        TaskExecutor, "_execute_code_task_legacy_cli", mock_legacy,
    )

    result = _run(executor._execute_code_task(
        task_title="test", task_description="use qwen code",
    ))

    assert legacy_called["value"] is True
    assert result.success is True


def test_local_execution_uses_docker_runtime_from_settings(make_executor, monkeypatch):
    executor = make_executor()
    captured = {}

    monkeypatch.setattr(
        "app.services.interpreter.task_executer.get_executor_settings",
        lambda: MagicMock(
            code_execution_backend="local",
            code_execution_local_runtime="docker",
            code_execution_docker_image="gagent-python-runtime:latest",
            code_execution_timeout=321,
        ),
    )

    async def _fake_execute_code_locally(**kwargs):
        captured.update(kwargs)
        return CodeExecutionOutcome(
            success=True,
            code="print('ok')",
            description="docker runtime",
            stdout="ok\n",
            attempts=1,
            execution_backend="docker",
            execution_status="completed",
        )

    monkeypatch.setattr(
        "app.services.interpreter.task_executer.execute_code_locally",
        _fake_execute_code_locally,
    )

    result = _run(executor._execute_code_task_local(
        task_title="test",
        task_description="use docker runtime",
    ))

    assert result.success is True
    assert captured["execution_backend"] == "docker"
    assert captured["docker_image"] == "gagent-python-runtime:latest"
    assert captured["timeout"] == 321


def test_local_execution_prefers_explicit_docker_overrides(tmp_path: Path, tmp_data_file: Path, monkeypatch):
    llm = MagicMock()
    llm.chat = MagicMock(return_value="mocked")
    executor = TaskExecutor(
        data_file_paths=[str(tmp_data_file)],
        output_dir=str(tmp_path / "output"),
        llm_service=llm,
        docker_image="custom:image",
        docker_timeout=900,
    )
    captured = {}

    monkeypatch.setattr(
        "app.services.interpreter.task_executer.get_executor_settings",
        lambda: MagicMock(
            code_execution_backend="local",
            code_execution_local_runtime="docker",
            code_execution_docker_image="gagent-python-runtime:latest",
            code_execution_timeout=321,
        ),
    )

    async def _fake_execute_code_locally(**kwargs):
        captured.update(kwargs)
        return CodeExecutionOutcome(
            success=True,
            code="print('ok')",
            description="docker runtime",
            stdout="ok\n",
            attempts=1,
            execution_backend="docker",
            execution_status="completed",
        )

    monkeypatch.setattr(
        "app.services.interpreter.task_executer.execute_code_locally",
        _fake_execute_code_locally,
    )

    result = _run(executor._execute_code_task_local(
        task_title="test",
        task_description="use docker override",
    ))

    assert result.success is True
    assert captured["execution_backend"] == "docker"
    assert captured["docker_image"] == "custom:image"
    assert captured["timeout"] == 900


# ---------------------------------------------------------------------------
# Persistent code file: verify file is written and retained
# ---------------------------------------------------------------------------

def test_code_file_persists_after_execution(make_executor, monkeypatch):
    """A generated task_code_<id>.py file should remain in output_dir after execution."""
    executor = make_executor()

    fake_code_resp = CodeTaskResponse(
        code="print('persist')",
        description="persistence test",
    )

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(
        f"{_CE_MOD}.LocalCodeInterpreter.run_file",
        lambda self, code_file: CodeExecutionResult(
            status="success", output="persist\n", error="", exit_code=0,
        ),
    )

    _run(executor._execute_code_task_local(
        task_title="test", task_description="persist check", task_id=7,
    ))

    code_files = sorted(Path(executor.output_dir).glob("task_7_code.py"))
    assert len(code_files) == 1
    content = code_files[0].read_text()
    assert "print('persist')" in content


# ---------------------------------------------------------------------------
# Error category is propagated through fix hint
# ---------------------------------------------------------------------------

def test_fix_hint_injected_for_missing_package(make_executor, monkeypatch):
    """When error_category=missing_package, fix hint mentions pip install."""
    executor = make_executor()

    fake_code_resp = CodeTaskResponse(code="import seaborn", description="import")
    fixed_resp = CodeTaskResponse(
        code="import subprocess; subprocess.check_call(['pip','install','seaborn'])\nimport seaborn",
        description="fixed",
    )

    call_count = {"exec": 0}
    captured_fix_kwargs = {}

    def mock_run_file(self, code_file):
        call_count["exec"] += 1
        if call_count["exec"] == 1:
            return CodeExecutionResult(
                status="failed", output="",
                error="ModuleNotFoundError: No module named 'seaborn'", exit_code=1,
            )
        return CodeExecutionResult(status="success", output="ok\n", error="", exit_code=0)

    def mock_fix_code(self, **kw):
        captured_fix_kwargs.update(kw)
        return fixed_resp

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", mock_fix_code)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter.run_file", mock_run_file)

    result = _run(executor._execute_code_task_local(
        task_title="test", task_description="use seaborn",
    ))

    assert result.success is True
    # Verify the fix hint was injected into the error message sent to LLM.
    error_sent = captured_fix_kwargs.get("error", "")
    assert "pip" in error_sent
    assert "seaborn" in error_sent


# ---------------------------------------------------------------------------
# Unified execution backend selection
# ---------------------------------------------------------------------------

def test_execute_code_locally_defaults_to_local_backend(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('ok')", description="local default")
    local_init = {}

    class FakeLocalInterpreter:
        def __init__(self, **kwargs):
            local_init.update(kwargs)

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            return CodeExecutionResult(status="success", output="ok\n", error="", exit_code=0)

    class UnexpectedDockerInterpreter:
        def __init__(self, **_kwargs):
            raise AssertionError("Docker interpreter should not be used by default")

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter", FakeLocalInterpreter)
    monkeypatch.setattr(f"{_CE_MOD}.DockerCodeInterpreter", UnexpectedDockerInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="local",
            task_description="default backend",
            work_dir=str(tmp_path),
        )
    )

    assert outcome.success is True
    assert outcome.execution_backend == "local"
    assert local_init["work_dir"] == str(tmp_path)


def test_execute_code_locally_uses_docker_backend_when_requested(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('ok')", description="docker backend")
    docker_init = {}
    readable_dir = tmp_path / "readable"
    readable_dir.mkdir()

    class UnexpectedLocalInterpreter:
        def __init__(self, **_kwargs):
            raise AssertionError("Local interpreter should not be used for docker backend")

    class FakeDockerInterpreter:
        def __init__(self, **kwargs):
            docker_init.update(kwargs)

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            return CodeExecutionResult(status="success", output="ok\n", error="", exit_code=0)

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter", UnexpectedLocalInterpreter)
    monkeypatch.setattr(f"{_CE_MOD}.DockerCodeInterpreter", FakeDockerInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="docker",
            task_description="docker backend",
            work_dir=str(tmp_path),
            execution_backend="docker",
            docker_image="custom:image",
            readable_dirs=[str(readable_dir)],
        )
    )

    assert outcome.success is True
    assert outcome.execution_backend == "docker"
    assert docker_init["image"] == "custom:image"
    assert docker_init["extra_read_dirs"] == [str(readable_dir)]


def test_execute_code_locally_stops_retrying_on_docker_runtime_failure(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('ok')", description="docker runtime fail")

    class FakeDockerInterpreter:
        def __init__(self, **_kwargs):
            pass

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            return CodeExecutionResult(
                status="error",
                output="",
                error="Docker image not found: missing:image",
                exit_code=-1,
                runtime_failure=True,
            )

    def _unexpected_fix(self, **_kwargs):
        raise AssertionError("Runtime failures must not trigger auto-fix retries")

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", _unexpected_fix)
    monkeypatch.setattr(f"{_CE_MOD}.DockerCodeInterpreter", FakeDockerInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="docker",
            task_description="runtime failure",
            work_dir=str(tmp_path),
            execution_backend="docker",
            docker_image="missing:image",
        )
    )

    assert outcome.success is False
    assert outcome.execution_backend == "docker"
    assert outcome.runtime_failure is True
    assert outcome.error_category == "docker_runtime_error"
    assert outcome.attempts == 1


def test_execute_code_locally_does_not_retry_on_warning_only_failure(tmp_path: Path, monkeypatch):
    fake_code_resp = CodeTaskResponse(code="print('ok')", description="warning only")

    class FakeLocalInterpreter:
        def __init__(self, **_kwargs):
            pass

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            return CodeExecutionResult(
                status="failed",
                output="",
                error=(
                    "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
                    "UserWarning: pkg_resources is deprecated as an API.\n"
                    "  from pkg_resources import get_distribution\n"
                ),
                exit_code=1,
            )

    def _unexpected_fix(self, **_kwargs):
        raise AssertionError("Warning-only noise must not trigger auto-fix")

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", _unexpected_fix)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter", FakeLocalInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="warning noise",
            task_description="do not retry",
            work_dir=str(tmp_path),
        )
    )

    assert outcome.success is False
    assert outcome.error_category == "non_fatal_warning_noise"
    assert outcome.attempts == 1


def test_execute_code_locally_uses_stdout_error_when_stderr_is_warning_only(
    tmp_path: Path,
    monkeypatch,
):
    fake_code_resp = CodeTaskResponse(code="print('ok')", description="stdout error")

    class FakeLocalInterpreter:
        def __init__(self, **_kwargs):
            pass

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            return CodeExecutionResult(
                status="failed",
                output=(
                    "ERROR: Missing required input files: ['filtered_cancer1.h5ad', 'metadata.csv']\n"
                    "This task cannot proceed without the filtered single-cell objects from upstream QC step.\n"
                ),
                error=(
                    "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
                    "UserWarning: pkg_resources is deprecated as an API.\n"
                    "  from pkg_resources import get_distribution\n"
                ),
                exit_code=1,
            )

    def _unexpected_fix(self, **_kwargs):
        raise AssertionError("stdout-derived actionable failures should not use fix_code when auto_fix=False")

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", _unexpected_fix)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter", FakeLocalInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="stdout error",
            task_description="respect stdout errors",
            work_dir=str(tmp_path),
            auto_fix=False,
        )
    )

    assert outcome.success is False
    assert outcome.error_category == "file_access"
    assert "Missing required input files" in str(outcome.error_summary or "")
    assert "pkg_resources is deprecated" not in str(outcome.error_summary or "")


def test_execute_code_locally_fix_prompt_uses_real_error_after_warning_noise(
    tmp_path: Path,
    monkeypatch,
):
    fake_code_resp = CodeTaskResponse(code="import seaborn", description="warning then import error")
    fixed_resp = CodeTaskResponse(code="print('fixed')", description="fixed")
    captured_fix_kwargs = {}
    call_count = {"exec": 0}

    class FakeLocalInterpreter:
        def __init__(self, **_kwargs):
            pass

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            call_count["exec"] += 1
            if call_count["exec"] == 1:
                return CodeExecutionResult(
                    status="failed",
                    output="",
                    error=(
                        "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
                        "UserWarning: pkg_resources is deprecated as an API.\n"
                        "  from pkg_resources import get_distribution\n"
                        "ModuleNotFoundError: No module named 'seaborn'\n"
                    ),
                    exit_code=1,
                )
            return CodeExecutionResult(status="success", output="fixed\n", error="", exit_code=0)

    def _capture_fix(self, **kwargs):
        captured_fix_kwargs.update(kwargs)
        return fixed_resp

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", _capture_fix)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter", FakeLocalInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="warning then error",
            task_description="use seaborn",
            work_dir=str(tmp_path),
        )
    )

    assert outcome.success is True
    error_sent = captured_fix_kwargs.get("error", "")
    assert "ModuleNotFoundError" in error_sent
    assert "pkg_resources is deprecated" not in error_sent


def test_execute_code_locally_fix_prompt_uses_stdout_failure_when_stderr_is_warning_only(
    tmp_path: Path,
    monkeypatch,
):
    fake_code_resp = CodeTaskResponse(code="print('ok')", description="stdout failure")
    fixed_resp = CodeTaskResponse(code="print('fixed')", description="fixed")
    captured_fix_kwargs = {}
    call_count = {"exec": 0}

    class FakeLocalInterpreter:
        def __init__(self, **_kwargs):
            pass

        def build_preamble(self):
            return ""

        def run_file(self, code_file):
            call_count["exec"] += 1
            if call_count["exec"] == 1:
                return CodeExecutionResult(
                    status="failed",
                    output=(
                        "ERROR: metadata.csv not found. Cannot proceed with integration.\n"
                        "Use the canonical metadata path instead of the temp run directory.\n"
                    ),
                    error=(
                        "/usr/local/lib/python3.10/site-packages/louvain/__init__.py:54: "
                        "UserWarning: pkg_resources is deprecated as an API.\n"
                        "  from pkg_resources import get_distribution\n"
                    ),
                    exit_code=1,
                )
            return CodeExecutionResult(status="success", output="fixed\n", error="", exit_code=0)

    def _capture_fix(self, **kwargs):
        captured_fix_kwargs.update(kwargs)
        return fixed_resp

    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.generate", lambda self, **kw: fake_code_resp)
    monkeypatch.setattr(f"{_CE_MOD}.CodeGenerator.fix_code", _capture_fix)
    monkeypatch.setattr(f"{_CE_MOD}.LocalCodeInterpreter", FakeLocalInterpreter)

    outcome = _run(
        execute_code_locally(
            task_title="stdout then fix",
            task_description="use canonical metadata path",
            work_dir=str(tmp_path),
        )
    )

    assert outcome.success is True
    error_sent = captured_fix_kwargs.get("error", "")
    assert "metadata.csv not found" in error_sent
    assert "pkg_resources is deprecated" not in error_sent


def test_build_coder_system_prompt_keeps_default_local_library_set() -> None:
    prompt = build_coder_system_prompt()
    assert "`pandas` - Data manipulation and analysis" in prompt
    assert "`scanpy`" not in prompt
    assert "`scrublet`" not in prompt


def test_build_coder_system_prompt_can_include_docker_only_libraries() -> None:
    prompt = build_coder_system_prompt(
        extra_libraries=[
            ("gseapy", "Gene set enrichment analysis workflows"),
            ("scanpy", "Single-cell analysis workflows"),
            ("harmonypy", "Harmony batch correction and integration"),
            ("bbknn", "Batch-balanced nearest neighbors integration"),
            ("dask", "Distributed and out-of-core array computation"),
            ("scrublet", "Doublet detection for single-cell data"),
        ],
        extra_system_tools=[
            ("samtools", "SAM/BAM/CRAM processing and statistics"),
            ("bedtools", "Genomic interval intersection and arithmetic"),
        ],
    )
    assert "`gseapy` - Gene set enrichment analysis workflows" in prompt
    assert "`scanpy` - Single-cell analysis workflows" in prompt
    assert "`harmonypy` - Harmony batch correction and integration" in prompt
    assert "`bbknn` - Batch-balanced nearest neighbors integration" in prompt
    assert "`dask` - Distributed and out-of-core array computation" in prompt
    assert "`scrublet` - Doublet detection for single-cell data" in prompt
    assert "`samtools` - SAM/BAM/CRAM processing and statistics" in prompt
    assert "`bedtools` - Genomic interval intersection and arithmetic" in prompt
    assert "invoke it through `subprocess.run(...)`" in prompt


def test_build_coder_system_prompt_includes_scRNA_dependency_guardrails() -> None:
    prompt = build_coder_system_prompt(
        extra_libraries=[
            ("scanpy", "Single-cell analysis workflows"),
            ("scrublet", "Doublet detection for single-cell data"),
        ],
    )

    assert "do NOT silently rewrite the task into a different upstream workflow" in prompt
    assert "prefer canonical absolute paths from the data directory" in prompt
    assert "Do NOT assume `adata.var['mt']` already exists" in prompt
    assert "fewer than 2 valid samples remain" in prompt
    assert "Only write `results/integrated_data.h5ad` when integration actually ran successfully" in prompt


# ---------------------------------------------------------------------------
# Skill context injection (unchanged)
# ---------------------------------------------------------------------------

def test_skill_context_content_is_injected(make_executor, monkeypatch):
    executor = make_executor()
    executor.skills_loader = SimpleNamespace(
        select_skills=lambda **kwargs: None,
        build_skill_context=lambda skill_ids, max_chars: None,
    )

    async def _fake_select_skills(**_kwargs):
        return SimpleNamespace(
            candidate_skill_ids=["bio-tools-router"],
            selected_skill_ids=["bio-tools-router"],
            selection_source="unit-test",
            selection_latency_ms=1.0,
        )

    executor.skills_loader.select_skills = _fake_select_skills
    executor.skills_loader.build_skill_context = lambda _skill_ids, max_chars: SimpleNamespace(
        content="Use seqkit for quick FASTA diagnostics.",
        injection_mode_by_skill={"bio-tools-router": "summary"},
        injected_chars=38,
    )

    hints, trace = _run(
        executor._select_skills_for_task(
            "Analyze FASTA",
            "Summarize the sequence file",
            tool_hints=["bio_tools"],
        )
    )

    assert "Use seqkit for quick FASTA diagnostics." in hints
    assert trace["selected_skill_ids"] == ["bio-tools-router"]


def test_execute_lightweight_overview_skips_llm_and_skill_selection(make_executor, monkeypatch):
    executor = make_executor()

    executor.llm_service.chat.side_effect = AssertionError("lightweight overview should not call LLM")

    async def _unexpected_select_skills(**_kwargs):
        raise AssertionError("lightweight overview should not select skills")

    monkeypatch.setattr(executor, "_select_skills_for_task", _unexpected_select_skills)

    result = _run(
        executor.execute(
            task_title="GVD Phage Dataset Overview",
            task_description="Provide a quick overview of the TSV files: rows, columns, schema, and key fields.",
        )
    )

    assert result.success is True
    assert result.task_type == TaskType.TEXT_ONLY
    assert result.text_response is not None
    assert "Dataset overview based on extracted metadata" in result.text_response
    assert "rows x" in result.text_response
    assert result.skill_trace["selection_source"] == "skipped_lightweight_overview"


# ---------------------------------------------------------------------------
# blocked_dependency pattern matching — regression tests for the filtering
# gap that caused Task 4 to burn 27 retries (April 2026 incident).
# ---------------------------------------------------------------------------

class TestBlockedDependencyPatternMatching:
    """Verify that common upstream-dependency error messages are classified
    as ``blocked_dependency`` rather than ``runtime_error``."""

    def test_fewer_than_2_valid_filtered_samples(self):
        """The exact message that triggered the April 2026 incident."""
        stderr = (
            "ERROR: Fewer than 2 valid filtered samples found. "
            "Cannot proceed with integration."
        )
        assert classify_error(stderr, 1) == "blocked_dependency"

    def test_missing_filtered_data_for_sample(self):
        stderr = (
            "ERROR: Missing filtered data for sample cancer2\n"
            "ERROR: Missing filtered data for sample cancer3"
        )
        assert classify_error(stderr, 1) == "blocked_dependency"

    def test_requires_output_from_task(self):
        stderr = (
            "This task requires the output from Task 3 (cell filtering and QC) "
            "to be available."
        )
        assert classify_error(stderr, 1) == "blocked_dependency"

    def test_ensure_task_completed(self):
        stderr = "Please ensure Task 3 has been completed successfully before running this task."
        assert classify_error(stderr, 1) == "blocked_dependency"

    def test_cannot_proceed_with_integration(self):
        stderr = "Cannot proceed with integration."
        assert classify_error(stderr, 1) == "blocked_dependency"

    def test_fewer_than_n_valid_regex(self):
        """Parameterised count: 'fewer than 5 valid ...' should also match."""
        stderr = "fewer than 5 valid filtered samples after QC"
        assert classify_error(stderr, 1) == "blocked_dependency"

    def test_original_pattern_still_works(self):
        """The original hardcoded pattern must not regress."""
        stderr = "BLOCKED_DEPENDENCY: fewer than 2 valid samples available."
        assert classify_error(stderr, 1) == "blocked_dependency"
