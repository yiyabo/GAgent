from app.services.plans.plan_executor import PlanExecutor


def _executor() -> PlanExecutor:
    return PlanExecutor(repo=object(), llm_service=object())


def test_build_tool_failure_error_prefers_explicit_error() -> None:
    executor = _executor()
    message = executor._build_tool_failure_error(
        "claude_code",
        {"success": False, "error": "explicit failure"},
    )
    assert message == "explicit failure"


def test_build_tool_failure_error_uses_exit_and_stream_context() -> None:
    executor = _executor()
    message = executor._build_tool_failure_error(
        "claude_code",
        {
            "success": False,
            "exit_code": 127,
            "stderr": "claude: command not found",
            "stdout": "attempted fallback command",
        },
    )

    assert message.startswith("claude_code failed:")
    assert "exit_code=127" in message
    assert "stderr=claude: command not found" in message
    assert "stdout=attempted fallback command" in message
