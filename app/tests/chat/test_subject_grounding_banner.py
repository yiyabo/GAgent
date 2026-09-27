"""User-facing failure caveat: raw tracebacks must become one readable line.

The 2026-09-27 AML session case: the caveat banner appended the full pandas
stack wall (truncated mid-word) — unreadable for users. The full traceback
still lives in logs and tool results (the model recovers with it); the banner
only carries the exception summary.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.routers.chat.subject_grounding import (
    _apply_grounded_local_answer,
    _humanize_failure_message,
)

_PANDAS_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/data/phage-agent/runtime/session_x/scratch/code_mode/kernels/abc/'
    'gagent_kernel_runner.py", line 89, in run_cell\n'
    '    exec(compile(request["code"], "<cell>", "exec"), GLOBALS)\n'
    '  File "<cell>", line 21, in <module>\n'
    '  File "<cell>", line 19, in knum\n'
    '  File "/usr/local/lib/python3.10/site-packages/pandas/core/generic.py", line 6665, in astype\n'
    "    new_data = self._mgr.astype(dtype=dtype, copy=copy, errors=errors)\n"
    '  File "/usr/local/lib/python3.10/site-packages/pandas/core/dtypes/astype.py", line 145, in _astype_float_to_int_nansafe\n'
    "    raise IntCastingNaNError(\n"
    "pandas.errors.IntCastingNaNError: Cannot convert non-finite values (NA or inf) to integer"
)

_TRUNCATED_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<cell>", line 19, in knum\n'
    '  File "/usr/local/lib/python3.10/site-packages/pandas/core/dtypes/astype.py", line 145, in _astype_float_to_int_nansafe\n'
    "    raise IntCastingNaNError("
)


def test_full_traceback_becomes_exception_summary_with_cell_line() -> None:
    out = _humanize_failure_message(_PANDAS_TRACEBACK)
    assert out == (
        "IntCastingNaNError: Cannot convert non-finite values (NA or inf) "
        "to integer（cell 第 19 行）"
    )


def test_truncated_traceback_falls_back_to_last_exception_mention() -> None:
    # Upstream may cut the wall before the final exception line — the summary
    # must still name the exception instead of dumping a stack fragment.
    out = _humanize_failure_message(_TRUNCATED_TRACEBACK)
    assert out == "IntCastingNaNError（cell 第 19 行）"


def test_exception_without_detail_keeps_type_only() -> None:
    out = _humanize_failure_message(
        "Traceback (most recent call last):\n  File \"<cell>\", line 3, in <module>\nValueError\n"
    )
    assert out == "ValueError（cell 第 3 行）"


def test_plain_message_passes_through() -> None:
    assert _humanize_failure_message("PubMed 检索超时，请稍后重试") == "PubMed 检索超时，请稍后重试"


def test_long_plain_message_is_capped() -> None:
    out = _humanize_failure_message("x" * 500)
    assert len(out) == 240
    assert out.endswith("…")


def test_empty_message_stays_empty() -> None:
    assert _humanize_failure_message("") == ""


def test_banner_carries_summary_not_the_stack_wall() -> None:
    agent = SimpleNamespace(
        history=[],
        extra_context={"last_failure_state": {"error_message": _PANDAS_TRACEBACK}},
    )
    out = _apply_grounded_local_answer(agent, "分析已完成。", SimpleNamespace())
    assert "⚠️ 本次操作未被验证成功：IntCastingNaNError: Cannot convert non-finite values" in out
    assert "Traceback" not in out
    assert "site-packages" not in out
