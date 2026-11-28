from app.services.plans import executor_deepthink


def test_execution_tools_drops_code_executor_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "0")
    names = executor_deepthink._execution_tools(["web_search", "code_executor"])
    assert "code_executor" not in names
    assert "web_search" in names


def test_execution_tools_keeps_code_executor_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("CODE_EXECUTOR_ENABLED", "1")
    names = executor_deepthink._execution_tools(["web_search", "code_executor"])
    assert "code_executor" in names
