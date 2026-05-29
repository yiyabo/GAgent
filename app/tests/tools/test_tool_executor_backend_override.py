from __future__ import annotations

from app.services.execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor


def test_unified_tool_executor_only_passes_explicit_code_backend_override() -> None:
    executor = UnifiedToolExecutor()
    context = ToolExecutionContext(plan_id=1, task_id=2)

    default_params = executor._normalize_params("code_executor", {"task": "run"}, context)
    delegated_params = executor._normalize_params(
        "code_executor",
        {"task": "run", "execution_backend": "local"},
        context,
    )
    non_code_params = executor._normalize_params(
        "bio_tools",
        {"operation": "help", "execution_backend": "local"},
        context,
    )

    assert "execution_backend" not in default_params
    assert delegated_params["execution_backend"] == "local"
    assert "execution_backend" not in non_code_params
