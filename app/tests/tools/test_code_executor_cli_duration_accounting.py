"""Wall-clock accounting for the qwen_code CLI delegation in code_executor.

Covers the `llm_usage_log` fields added for sub-agent cost governance:
`duration_ms` (CLI wall clock), `tool_name`, and `call_status` — on both the
successful and the failing CLI path.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


class _FakeClock:
    """Deterministic `time` module stand-in: every perf_counter() advances the clock."""

    def __init__(self, start: float = 1000.0, step: float = 0.25) -> None:
        self._now = start
        self._step = step

    def perf_counter(self) -> float:
        value = self._now
        self._now += self._step
        return value


class _FakeStream:
    def __init__(self, chunks: list[bytes], delay_s: float = 0.0) -> None:
        self._chunks = list(chunks)
        self._delay_s = delay_s

    async def read(self, _chunk_size: int = 65536) -> bytes:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
            self._delay_s = 0.0
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes = b"", delay_s: float = 0.0) -> None:
        self.returncode = returncode
        self.stdout = _FakeStream([stdout] if stdout else [], delay_s=delay_s)
        self.stderr = _FakeStream([], delay_s=delay_s)
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


class _NeverEndingStream:
    def __init__(self, process: "_HangingProcess") -> None:
        self._process = process

    async def read(self, _chunk_size: int = 65536) -> bytes:
        while not self._process.killed:
            await asyncio.sleep(0.001)
        return b""


class _HangingProcess:
    """CLI process that never emits output and never exits on its own."""

    returncode: int | None = None

    def __init__(self) -> None:
        self.killed = False
        self.stdout = _NeverEndingStream(self)
        self.stderr = _NeverEndingStream(self)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else -9

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


def _install_qwen_cli_stubs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    returncode: int,
    stdout: bytes = b"",
    delay_s: float = 0.0,
    hang: bool = False,
    no_output_timeout_s: float | None = None,
) -> list:
    """Stub out the qwen_code CLI plumbing so the handler runs without a real CLI."""
    from tool_box.tools_impl import code_executor as code_executor_module

    monkeypatch.setenv("QWEN_CODE_MODEL", "test-model")
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_code_executor_backend",
        lambda _task: ("qwen_code", "qwen_primary", "code task routed to qwen_code primary lane"),
    )
    monkeypatch.setattr(
        code_executor_module,
        "_resolve_runtime_session_dir",
        lambda _session_id: tmp_path / "session-x",
    )
    monkeypatch.setattr(code_executor_module, "_build_execution_spec", lambda *_a, **_k: {})
    monkeypatch.setattr(code_executor_module, "_qwen_code_cli_available", lambda: True)
    monkeypatch.setattr(code_executor_module, "_resolve_cli_retry_policy", lambda: (0, 0.0))
    if no_output_timeout_s is not None:
        monkeypatch.setattr(
            code_executor_module,
            "_resolve_qwen_cli_no_output_timeout_seconds",
            lambda: no_output_timeout_s,
        )
        monkeypatch.setattr(
            code_executor_module,
            "_resolve_qwen_completed_output_exit_check_seconds",
            lambda: 0.01,
        )
        monkeypatch.setattr(
            code_executor_module,
            "_resolve_qwen_process_kill_wait_seconds",
            lambda: 0.01,
        )

    class _Router:
        def get_task_output_dir(self, session_id, task_id, ancestor_chain, create=True):
            out = tmp_path / "raw" / str(session_id) / f"task_{task_id}"
            if create:
                out.mkdir(parents=True, exist_ok=True)
            return out

        def get_tmp_output_dir(self, session_id, run_id, create=True):
            out = tmp_path / "tmp" / str(session_id) / str(run_id)
            if create:
                out.mkdir(parents=True, exist_ok=True)
            return out

    monkeypatch.setattr(code_executor_module, "get_path_router", lambda: _Router())

    class _Driver:
        def get_execution_lock(self, _session_id):
            return asyncio.Lock()

        async def ensure_container(self, *_args, **_kwargs):
            return "qwen-test-container"

    monkeypatch.setattr(
        "app.services.terminal.qwen_session_driver.get_qwen_session_driver",
        lambda: _Driver(),
    )

    created: list = []

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        if hang:
            process: object = _HangingProcess()
        else:
            process = _FakeProcess(returncode=returncode, stdout=stdout, delay_s=delay_s)
        created.append(process)
        return process

    monkeypatch.setattr(
        code_executor_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    return created


def _run_handler() -> dict:
    from tool_box.tools_impl import code_executor as code_executor_module

    return asyncio.run(
        code_executor_module.code_executor_handler(
            task="produce a report",
            allowed_tools="Bash,Write,Read",
            session_id="session-x",
            plan_id=1,
            task_id=2,
        )
    )


def _init_usage_table() -> None:
    from app.repository.llm_usage import init_llm_usage_table

    init_llm_usage_table()


def _read_usage_rows(session_id: str) -> list[dict]:
    from app.repository.llm_usage import get_usage_calls

    return get_usage_calls(session_id=session_id)


@pytest.fixture
def isolated_usage_db(tmp_path: Path):
    """Point the shared SQLite pool at a throwaway ledger instead of the repo DB."""
    from app.database_pool import close_connection_pool, initialize_connection_pool

    initialize_connection_pool(db_path=str(tmp_path / "llm_usage_ledger.db"))
    try:
        yield tmp_path / "llm_usage_ledger.db"
    finally:
        close_connection_pool()


def test_record_external_cli_usage_forwards_duration_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_box.tools_impl import code_executor as code_executor_module

    captured: dict = {}

    def _fake_log_llm_usage(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.repository.llm_usage.log_llm_usage", _fake_log_llm_usage)

    code_executor_module._record_external_cli_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=100,
        completion_tokens=25,
        session_id="session_x",
        plan_id=122,
        task_id=14,
        call_purpose="qwen_code_cli_execution",
        duration_ms=12345.5,
        run_id="20260925_120000_000000_deadbeef",
        tool_name="code_executor",
        call_status="error",
    )

    assert captured["duration_ms"] == 12345.5
    assert captured["run_id"] == "20260925_120000_000000_deadbeef"
    assert captured["tool_name"] == "code_executor"
    assert captured["call_status"] == "error"

    captured.clear()
    code_executor_module._record_external_cli_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=1,
        completion_tokens=1,
        session_id="session_x",
        plan_id=122,
        task_id=14,
        call_purpose="qwen_code_cli_execution",
    )

    assert captured["duration_ms"] is None
    assert captured["run_id"] is None
    assert captured["tool_name"] is None
    assert captured["call_status"] is None


def test_cli_delegation_duration_recorded_on_success(
    monkeypatch: pytest.MonkeyPatch,
    isolated_usage_db: Path,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import code_executor as code_executor_module

    monkeypatch.setattr(code_executor_module, "time", _FakeClock())
    created = _install_qwen_cli_stubs(monkeypatch, tmp_path, returncode=0, stdout=b'{"ok": true}')
    _init_usage_table()

    result = _run_handler()

    assert created, "the fake CLI process must have been spawned"
    assert result["success"] is True
    assert result["cli_usage"] is not None

    rows = _read_usage_rows("session-x")
    assert len(rows) == 1
    row = rows[0]
    assert row["call_purpose"] == "qwen_code_cli_execution"
    assert row["tool_name"] == "code_executor"
    assert row["call_status"] == "ok"
    assert row["billing_key"] == "coding_agent.qwen_code_cli"
    # The ledger row is attributable to the delegation run reported in the payload.
    assert row["run_id"] == result["run_id"]
    # One measured delegation span; the fake clock advances 0.25s per reading.
    assert row["duration_ms"] == pytest.approx(250.0)


def test_cli_delegation_duration_recorded_on_cli_failure(
    monkeypatch: pytest.MonkeyPatch,
    isolated_usage_db: Path,
    tmp_path: Path,
) -> None:
    created = _install_qwen_cli_stubs(
        monkeypatch,
        tmp_path,
        returncode=1,
        stdout=b"cli said nothing useful",
        delay_s=0.05,
    )
    _init_usage_table()

    result = _run_handler()

    assert created and created[0].killed is False
    assert result["success"] is False

    rows = _read_usage_rows("session-x")
    assert len(rows) == 1
    row = rows[0]
    assert row["call_purpose"] == "qwen_code_cli_execution"
    assert row["tool_name"] == "code_executor"
    assert row["call_status"] == "error"
    assert row["duration_ms"] is not None
    assert row["duration_ms"] >= 50.0
    assert row["duration_ms"] < 60_000.0


def test_cli_delegation_duration_recorded_on_no_output_timeout(
    monkeypatch: pytest.MonkeyPatch,
    isolated_usage_db: Path,
    tmp_path: Path,
) -> None:
    created = _install_qwen_cli_stubs(
        monkeypatch,
        tmp_path,
        returncode=0,
        hang=True,
        no_output_timeout_s=0.01,
    )
    _init_usage_table()

    result = _run_handler()

    assert created and created[0].killed is True
    assert result["success"] is False
    assert result["runtime_failure"] is True

    rows = _read_usage_rows("session-x")
    assert len(rows) == 1
    row = rows[0]
    assert row["call_status"] == "error"
    assert row["duration_ms"] is not None
    assert row["duration_ms"] > 0.0
    assert row["duration_ms"] < 60_000.0
