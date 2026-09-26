"""The local lane's own instruction must not trip its own input-path scan.

``_execute_task_locally`` assembles the prompt given to the code-generating model
and hands the *same* text to ``execute_code_locally``, which scans it for
absolute paths that do not exist and refuses to run when it finds one
(``_find_missing_absolute_input_paths``). While the "use relative paths" rule was
illustrated with ``/home/.../results`` and ``/home/.../output``, the scanner found
exactly those two strings, so **every** local-lane call was refused with
BLOCKED_DEPENDENCY — production evidence 2026-09-26: the refusal names those two
paths and nothing else, because the work dirs are exempt via ``writable_roots``.

These tests are the drift lock: they compare the instruction we generate against
the scanner that consumes it, which is the pair that silently disagreed.
"""

from __future__ import annotations

from app.services.interpreter.code_execution import _find_missing_absolute_input_paths
from tool_box.tools_impl.code_executor_backend import build_local_task_description

_WORK_DIR = "/tmp/cc_local_instruction_test"


def _description(work_dir: str = _WORK_DIR) -> str:
    return build_local_task_description(
        task="按类别汇总订单并写出 results/category_summary.csv，回复里给出总金额最高的类别",
        work_dir=work_dir,
        results_dir=f"{work_dir}/results",
    )


def test_instruction_keeps_the_task_and_the_paths_it_advertises() -> None:
    text = _description()

    assert "按类别汇总订单" in text
    assert f"Working directory: {_WORK_DIR}" in text
    assert f"Save outputs to: {_WORK_DIR}/results" in text
    # The rule itself must survive any rewording, or the model loses the guidance.
    assert "RELATIVE paths" in text


def test_instruction_contains_no_path_the_scanner_would_block() -> None:
    """The exact regression: our guidance text must not read as a missing input."""
    missing = _find_missing_absolute_input_paths(
        _description(), writable_roots=(_WORK_DIR,)
    )

    assert missing == [], f"the local lane would block itself on: {missing}"


def test_a_genuine_missing_input_is_still_blocked() -> None:
    """Guard the guard: the protection this bug abused must still work."""
    missing = _find_missing_absolute_input_paths(
        "read /nonexistent/input/orders2.csv and summarise it",
        writable_roots=(_WORK_DIR,),
    )

    assert missing == ["/nonexistent/input/orders2.csv"]
