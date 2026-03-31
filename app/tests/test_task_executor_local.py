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
