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
    CodeExecutionOutcome,
    execute_code_locally,
)
from app.services.interpreter.prompts.coder_prompt import build_coder_system_prompt

# Module where execute_code_locally's internals live (for monkeypatching).
_CE_MOD = "app.services.interpreter.code_execution"


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear lru_cache on executor settings to avoid cross-test pollution."""
    from app.config.executor_config import get_executor_settings
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
