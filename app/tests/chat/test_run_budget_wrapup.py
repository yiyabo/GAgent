"""At 80% of the run budget the model is told once to wrap up (Hermes parity).

The notice is appended to the newest tool result, not sent as a new user turn,
so the prompt prefix before it stays byte-identical for the provider cache.
"""

from __future__ import annotations

import asyncio
import time

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think import wrapup
from app.services.deep_think_agent import DeepThinkAgent
from app.services.run_budget import RunBudget, bind_run_budget, reset_run_budget


def _bind(total: float, elapsed: float):
    budget = RunBudget(total)
    budget.started_at = time.monotonic() - elapsed
    return bind_run_budget(budget)


def test_due_only_after_eighty_percent_of_the_work_window() -> None:
    assert wrapup.wrapup_due() is False  # no run budget -> never
    handle = _bind(1000, 700)  # work window = 1000 - 10 close reserve = 990; 80% = 792
    try:
        assert wrapup.wrapup_due() is False
    finally:
        reset_run_budget(handle)
    handle = _bind(1000, 800)
    try:
        assert wrapup.wrapup_due() is True
    finally:
        reset_run_budget(handle)


def test_notice_goes_on_the_newest_tool_result_only() -> None:
    messages = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "a", "content": "old result"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "b", "content": "new result"},
    ]
    assert wrapup.append_wrapup_notice(messages) is True
    assert messages[1]["content"] == "old result"
    assert messages[3]["content"].startswith("new result")
    assert "Run budget notice" in messages[3]["content"]
    assert len(messages) == 4


def test_no_tool_result_yet_means_no_notice() -> None:
    messages = [{"role": "user", "content": "q"}]
    assert wrapup.append_wrapup_notice(messages) is False
    assert messages == [{"role": "user", "content": "q"}]


def test_loop_adds_the_notice_once_when_the_budget_is_mostly_spent() -> None:
    class LLM:
        def __init__(self) -> None:
            self.calls: list[list[dict]] = []
            self._n = 0

        async def stream_chat_with_tools_async(self, **kwargs):
            self.calls.append([dict(m) for m in kwargs["messages"]])
            self._n += 1
            if self._n > 4:
                return NativeStreamResult(
                    content="done",
                    tool_calls=[NativeToolCall(id="f", name="submit_final_answer",
                                               arguments={"answer": "All finished.", "confidence": 0.9})],
                )
            return NativeStreamResult(
                content=f"step {self._n}",
                tool_calls=[NativeToolCall(id=f"t{self._n}", name="file_operations",
                                           arguments={"operation": "read", "path": f"/x/{self._n}"})],
            )

    async def executor(name, params):
        return {"success": True, "summary": f"read {params['path']}"}

    llm = LLM()
    agent = DeepThinkAgent(llm_client=llm, available_tools=["file_operations"], tool_executor=executor,
                           max_iterations=10, request_profile={"intent_type": "chat"})
    handle = _bind(1000, 850)
    try:
        result = asyncio.run(agent.think("read some files"))
    finally:
        reset_run_budget(handle)

    assert result.final_answer.startswith("All finished.")

    def notices(call):
        return [m for m in call if "Run budget notice" in str(m.get("content"))]

    assert notices(llm.calls[0]) == []  # no tool result existed yet
    assert len(notices(llm.calls[1])) == 1
    assert notices(llm.calls[1])[0]["role"] == "tool"
    assert len(notices(llm.calls[-1])) == 1  # still exactly one, never repeated
