from __future__ import annotations

from app.services.execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor
from tool_box.tools_impl.code_executor import _build_qwen_code_command


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


def test_qwen_code_prompt_includes_final_response_and_rerun_contract() -> None:
    command = _build_qwen_code_command(
        task="Create answer.txt",
        work_dir="/tmp/task-work",
        file_prefix="task_1",
        output_format="json",
        allowed_tools=["Read", "Bash"],
        allowed_dirs=[],
        model=None,
        debug=False,
        allowed_dirs_info="",
        execution_spec={
            "task_id": 1,
            "task_name": "Create answer",
            "task_instruction": "Create answer.txt",
            "acceptance_criteria": {
                "blocking": True,
                "checks": [{"type": "file_nonempty", "path": "answer.txt"}],
            },
        },
    )

    prompt = command[command.index("-p") + 1]

    assert "Final response contract:" in prompt
    assert '"status": "COMPLETED | BLOCKED_DEPENDENCY | FAILED | PARTIAL"' in prompt
    assert "Rerun/update mode:" in prompt
    assert "produced_files" in prompt
    assert "acceptance_check" in prompt
