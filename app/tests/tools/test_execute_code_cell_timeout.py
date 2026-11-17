"""Agent-requested per-cell timeouts for execute_code (p67).

The agent may judge that a long download or computation is genuinely needed and
ask for more time for one cell instead of splitting the work or giving up.
"""

from __future__ import annotations

import pytest

from app.services.execution.tool_executor import UnifiedToolExecutor
from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import config
from tool_box.tools_impl.execute_code import kernel as kernel_module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(config.ENV_CELL_TIMEOUT, raising=False)
    monkeypatch.delenv(config.ENV_MAX_CELL_TIMEOUT, raising=False)
    yield
    kernel_module.shutdown_all_kernels()


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, 300),
        (True, 300),
        ("abc", 300),
        (60, 300),  # never shorter than the default
        (1800, 1800),
        ("3600", 3600),
        (99999, 7200),  # clamped to the maximum
    ],
)
def test_resolve_cell_timeout(requested, expected):
    assert config.resolve_cell_timeout(requested) == expected


def test_max_cell_timeout_is_operator_configurable(monkeypatch):
    monkeypatch.setenv(config.ENV_MAX_CELL_TIMEOUT, "900")
    assert config.resolve_cell_timeout(5000) == 900
    monkeypatch.setenv(config.ENV_CELL_TIMEOUT, "1200")
    # The maximum never drops below the default.
    assert config.max_cell_timeout_seconds() == 1200


def test_outer_tool_timeout_follows_requested_cell_limit():
    assert UnifiedToolExecutor.timeout_for("execute_code", {"code": "x"}, 60) == 420
    assert UnifiedToolExecutor.timeout_for("execute_code", {"code": "x", "timeout_seconds": 3600}, 60) == 3720
    assert UnifiedToolExecutor.timeout_for("execute_code", {"timeout_seconds": 99999}, 60) == 7320
    assert UnifiedToolExecutor.timeout_for("web_search", {"timeout_seconds": 9999}, 60) == 600
    assert UnifiedToolExecutor.timeout_for("unknown_tool", None, 42) == 42


@pytest.mark.timeout(60)
def test_requested_timeout_lets_a_cell_outlive_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_CELL_TIMEOUT, "1")
    monkeypatch.setenv("CODE_MODE_KILL_GRACE_SECONDS", "0.5")
    ctx = ToolContext(session_id="p67-timeout", work_dir=str(tmp_path))

    def run(timeout_seconds=None):
        return kernel_module.run_cell(
            "import time; time.sleep(2.5); print('done')",
            session_id="p67-timeout",
            work_dir=str(tmp_path),
            reset=False,
            tool_context=ctx,
            timeout_seconds=timeout_seconds,
        )

    short = run()
    assert short["status"] == "timeout"
    assert "timeout_seconds" in short["error"]
    long = run(timeout_seconds=20)
    assert long["status"] == "success", long
    assert long["output"].strip() == "done"
