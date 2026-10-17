"""Infrastructure failure is not content approval, with either completion policy."""

from dataclasses import replace
import json

import httpx
import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.execution.llm_observation import ObserverRejected
from app.services.foundation.settings import get_settings
from app.tests.plan.test_executor_task_completion_policy import task_case, _config


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Provider:
    def __init__(self, protocol, target, failure, recover=False, partial=False):
        self.protocol, self.target, self.failure = protocol, target, failure
        self.recover, self.partial, self.calls = recover, partial, 0

    def response(self):
        self.calls += 1
        if self.partial and self.calls == 1:
            return "execute_code", {
                "code": f'from pathlib import Path\nPath({str(self.target)!r}).write_text("partial evidence")'
            }
        if not self.recover or self.calls == 1:
            raise self.failure("Controlled model outage")
        if self.calls == 2:
            return "execute_code", {
                "code": f"from pathlib import Path\nPath({str(self.target)!r}).write_text('{{\"count\":2}}')"
            }
        return "submit_final_answer", {"answer": "Saved the requested result: " + str(self.target), "confidence": 1.0}

    async def stream_chat_with_tools_async(self, **kwargs):
        name, args = self.response()
        return NativeStreamResult(
            content="Continue the assigned task.",
            finish_reason="tool_calls",
            tool_calls=[NativeToolCall(str(self.calls), name, args)],
        )

    async def stream_chat_async(self, **kwargs):
        name, args = self.response()
        yield json.dumps(
            {
                "thinking": "Continue the assigned task.",
                "action": None if name == "submit_final_answer" else {"tool": name, "params": args},
                "final_answer": args if name == "submit_final_answer" else None,
            }
        )

    async def chat_async(self, **kwargs):
        name, args = self.response()
        return args.get("answer", name)


@pytest.mark.parametrize("protocol", ["native", "strict"])
@pytest.mark.parametrize("acceptance", ["0", "1"])
@pytest.mark.parametrize("failure", [httpx.ConnectError, ObserverRejected])
def test_failed_model_cannot_complete_a_plan_task(task_case, monkeypatch, protocol, acceptance, failure):
    monkeypatch.setenv("PLAN_ACCEPTANCE_ENABLED", acceptance)
    get_settings.cache_clear()
    executor, _, plan, task, target, _ = task_case(protocol=protocol, produce=False)
    executor._settings = replace(executor._settings, deep_think_max_iterations=6)
    provider = Provider(protocol, target, failure)
    if protocol == "strict":
        provider.stream_chat_with_tools_async = None
    executor._llm._llm = provider
    result = executor.execute_task(plan, task, config=_config(max_retries=1))
    assert result.status == "failed", result.to_dict()
    assert not target.exists()
    assert provider.calls == (1 if failure is ObserverRejected else 5)
    if failure is not ObserverRejected:
        assert result.metadata["termination_reason"] == "llm_unavailable"


@pytest.mark.parametrize("protocol", ["native", "strict"])
def test_successful_retry_clears_transient_model_error(task_case, monkeypatch, protocol):
    monkeypatch.setenv("PLAN_ACCEPTANCE_ENABLED", "0")
    get_settings.cache_clear()
    executor, _, plan, task, target, _ = task_case(protocol=protocol, produce=False)
    provider = Provider(protocol, target, httpx.ConnectError, recover=True)
    if protocol == "strict":
        provider.stream_chat_with_tools_async = None
    executor._llm._llm = provider
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "completed", result.to_dict()
    assert json.loads(target.read_text()) == {"count": 2}
    assert result.metadata.get("termination_reason") is None


def test_fatal_model_error_retains_prior_files(task_case, monkeypatch):
    monkeypatch.setenv("PLAN_ACCEPTANCE_ENABLED", "0")
    get_settings.cache_clear()
    executor, _, plan, task, target, _ = task_case(produce=False)
    executor._settings = replace(executor._settings, deep_think_max_iterations=8)
    executor._llm._llm = Provider("native", target, httpx.ConnectError, partial=True)
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "failed"
    assert target.read_text() == "partial evidence"
    assert result.metadata["termination_reason"] == "llm_unavailable"


@pytest.mark.parametrize("protocol", ["native", "strict"])
@pytest.mark.parametrize("failure", [httpx.ConnectError, ObserverRejected])
def test_failure_during_final_synthesis_is_not_success(task_case, monkeypatch, protocol, failure):
    monkeypatch.setenv("PLAN_ACCEPTANCE_ENABLED", "0")
    get_settings.cache_clear()
    executor, _, plan, task, target, _ = task_case(protocol=protocol, produce=False)
    executor._settings = replace(executor._settings, deep_think_max_iterations=1)
    provider = Provider(protocol, target, failure, partial=True)
    if protocol == "strict":
        provider.stream_chat_with_tools_async = None
    executor._llm._llm = provider
    result = executor.execute_task(plan, task, config=_config())
    assert result.status == "failed", result.to_dict()
    assert target.read_text() == "partial evidence"
    if failure is not ObserverRejected:
        assert result.metadata["termination_reason"] == "llm_unavailable"
