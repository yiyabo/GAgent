"""Tests for Phase 4 — ContextWindowManager with token-aware compaction.

Validates:
1. Token estimation works for various content types
2. ContextUsage thresholds (warning/critical) fire correctly
3. Compaction triggers at warning threshold, preserves system + recent messages
4. Compaction skips when below threshold or too few messages
5. Summarizer failure is handled gracefully
"""

from __future__ import annotations

import pytest

from app.services.context.context_manager import (
    ContextWindowManager,
    ContextUsage,
    build_summarization_prompt,
    estimate_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    get_context_window,
)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

class TestTokenEstimation:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_english_text_reasonable_estimate(self):
        text = "Hello world, this is a test sentence for token counting."
        tokens = estimate_tokens(text)
        assert 8 <= tokens <= 20  # ~12 tokens expected

    def test_chinese_text_reasonable_estimate(self):
        text = "你好世界，这是一个测试句子。"
        tokens = estimate_tokens(text)
        assert 5 <= tokens <= 30

    def test_message_includes_framing_overhead(self):
        msg = {"role": "user", "content": "hi"}
        tokens = estimate_message_tokens(msg)
        assert tokens > estimate_tokens("hi")  # framing adds overhead

    def test_message_with_tool_calls(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"test"}'}}
            ],
        }
        tokens = estimate_message_tokens(msg)
        assert tokens > 10  # tool call JSON adds tokens

    def test_messages_total(self):
        msgs = [
            {"role": "system", "content": "You are an assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        total = estimate_messages_tokens(msgs)
        assert total > 15


# ---------------------------------------------------------------------------
# Model context window
# ---------------------------------------------------------------------------

class TestContextWindow:
    def test_known_model(self):
        assert get_context_window("qwen3.6-plus") == 1000000

    def test_unknown_model_returns_default(self):
        assert get_context_window("unknown-model-xyz") == 131072

    def test_prefix_match(self):
        assert get_context_window("qwen-plus-latest-2026") == 131072

    def test_empty_model(self):
        assert get_context_window("") == 131072


# ---------------------------------------------------------------------------
# Context usage
# ---------------------------------------------------------------------------

class TestContextUsage:
    def test_below_warning(self):
        mgr = ContextWindowManager(max_context_tokens=1000, warning_ratio=0.75)
        msgs = [{"role": "user", "content": "short"}]
        usage = mgr.check_usage(msgs)
        assert usage.warning is False
        assert usage.critical is False
        assert usage.remaining_tokens > 0

    def test_at_warning(self):
        mgr = ContextWindowManager(max_context_tokens=50, warning_ratio=0.5)
        # Build a message big enough to exceed 50% of 50 tokens (~25 tokens needed)
        msgs = [{"role": "user", "content": "word " * 60}]
        usage = mgr.check_usage(msgs)
        assert usage.warning is True

    def test_remaining_tokens(self):
        usage = ContextUsage(
            used_tokens=800,
            max_tokens=1000,
            ratio=0.8,
            warning=True,
            critical=False,
        )
        assert usage.remaining_tokens == 200


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

class TestCompaction:
    @pytest.mark.asyncio
    async def test_no_compaction_below_threshold(self):
        mgr = ContextWindowManager(max_context_tokens=100000)
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]

        async def should_not_be_called(text: str) -> str:
            raise AssertionError("Summarizer should not be called")

        result = await mgr.compact_if_needed(msgs, summarizer=should_not_be_called)
        assert result is msgs  # unchanged

    @pytest.mark.asyncio
    async def test_no_compaction_with_few_messages(self):
        mgr = ContextWindowManager(max_context_tokens=100, warning_ratio=0.01)
        msgs = [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "y"},
        ]

        async def should_not_be_called(text: str) -> str:
            raise AssertionError("Summarizer should not be called")

        result = await mgr.compact_if_needed(msgs, summarizer=should_not_be_called)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_compaction_preserves_system_and_recent(self):
        mgr = ContextWindowManager(max_context_tokens=50, warning_ratio=0.01)

        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
        ]
        # Add enough messages to trigger compaction
        for i in range(12):
            msgs.append({"role": "user", "content": f"Message {i} with some content."})
            msgs.append({"role": "assistant", "content": f"Response {i} with details."})

        async def fake_summarizer(text: str) -> str:
            return "Summary of earlier conversation."

        result = await mgr.compact_if_needed(msgs, summarizer=fake_summarizer)

        # The one and only system message is the untouched system prompt.
        assert result[0]["role"] == "system"
        assert "helpful assistant" in result[0]["content"]
        assert [m["role"] for m in result].count("system") == 1

        # Summary inserted; role alternates against the retained tail (whose
        # first message here is a user turn), never a second "system".
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"
        assert "Summary" in result[1]["content"]
        assert "Context Summary" in result[1]["content"]

        # Recent messages preserved (last KEEP_RECENT)
        assert len(result) == 1 + 1 + mgr.KEEP_RECENT  # system + summary + recent
        assert result[-1]["content"].startswith("Response")

    @pytest.mark.asyncio
    async def test_compaction_survives_summarizer_failure(self):
        mgr = ContextWindowManager(max_context_tokens=50, warning_ratio=0.01)
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"msg {i}"})

        async def failing_summarizer(text: str) -> str:
            raise RuntimeError("LLM down")

        result = await mgr.compact_if_needed(msgs, summarizer=failing_summarizer)
        assert result is msgs  # unchanged on failure

    @pytest.mark.asyncio
    async def test_force_compaction(self):
        mgr = ContextWindowManager(max_context_tokens=100000)
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"msg {i}"})

        async def summarizer(text: str) -> str:
            return "Forced summary."

        result = await mgr.compact_if_needed(msgs, summarizer=summarizer, force=True)
        assert len(result) < len(msgs)
        assert "Forced summary" in result[1]["content"]

    @pytest.mark.asyncio
    async def test_compaction_count_increments(self):
        mgr = ContextWindowManager(max_context_tokens=50, warning_ratio=0.01)
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"msg {i}"})

        async def summarizer(text: str) -> str:
            return "Summary."

        assert mgr._compaction_count == 0
        await mgr.compact_if_needed(msgs, summarizer=summarizer)
        assert mgr._compaction_count == 1


# ---------------------------------------------------------------------------
# Summarization prompt
# ---------------------------------------------------------------------------

class TestSummarizationPrompt:
    def test_prompt_includes_conversation(self):
        prompt = build_summarization_prompt("User asked about files.")
        assert "User asked about files." in prompt
        assert "Concise summary" in prompt
        assert "bullet points" in prompt


class TestToolConversationCompaction:
    @pytest.mark.asyncio
    async def test_reused_ids_keep_each_result_with_its_preceding_call(self):
        def call(path):
            return {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_0", "type": "function", "function": {
                    "name": "read_file", "arguments": f'{{"path":"{path}"}}',
                }}],
            }

        messages = [
            {"role": "system", "content": "Read both inputs."},
            {"role": "user", "content": "Start."},
            call("first.csv"),
            {"role": "tool", "tool_call_id": "call_0", "content": "first result"},
            {"role": "user", "content": "Read the next file."},
            {"role": "assistant", "content": "Continuing."},
            call("second.csv"),
            {"role": "tool", "tool_call_id": "call_0", "content": "second result"},
            {"role": "assistant", "content": "Both files read."},
        ]

        async def summarize(_text):
            return "The user requested both inputs."

        compacted = await ContextWindowManager().compact_if_needed(
            messages, summarizer=summarize, force=True,
        )
        pending = set()
        calls_kept = 0
        for message in compacted:
            for tool_call in message.get("tool_calls") or []:
                pending.add(tool_call["id"])
                calls_kept += 1
            if message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
        assert calls_kept == 2
        assert not pending

    @pytest.mark.asyncio
    async def test_retained_results_keep_their_entire_tool_call_batch(self):
        messages = [
            {"role": "system", "content": "Keep the active task."},
            {"role": "user", "content": "Inspect both inputs."},
            {
                "role": "assistant", "content": "",
                "tool_calls": [
                    {"id": "read-a", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.csv"}'}},
                    {"id": "read-b", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"b.csv"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "read-a", "content": "rows=10"},
            {"role": "tool", "tool_call_id": "read-b", "content": "rows=20"},
            {"role": "user", "content": "Compare them."},
            {"role": "assistant", "content": "The second input is larger."},
            {"role": "user", "content": "Keep working."},
            {"role": "assistant", "content": "I will use both inputs."},
        ]

        async def summarize(_text):
            return "The user requested an input comparison."

        compacted = await ContextWindowManager().compact_if_needed(
            messages, summarizer=summarize, force=True,
        )
        calls = {
            call["id"]
            for message in compacted
            for call in message.get("tool_calls", [])
        }
        results = {message["tool_call_id"] for message in compacted if message["role"] == "tool"}
        assert results == calls == {"read-a", "read-b"}
        assert compacted[-1] == messages[-1]

    @pytest.mark.asyncio
    async def test_summary_receives_tool_names_arguments_and_result_identity(self):
        messages = [
            {"role": "system", "content": "Preserve file provenance."},
            {"role": "user", "content": "Inspect this file."},
            {
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": "read-origin", "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"cohort/input.csv"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "read-origin", "content": "sample_count=10"},
            *[{"role": "user", "content": f"Follow-up {i}"} for i in range(8)],
        ]
        summary_inputs = []

        async def summarize(text):
            summary_inputs.append(text)
            return "The file cohort/input.csv has ten samples."

        compacted = await ContextWindowManager().compact_if_needed(
            messages, summarizer=summarize, force=True,
        )
        assert len(compacted) < len(messages)
        assert len(summary_inputs) == 1
        assert "read_file" in summary_inputs[0]
        assert "cohort/input.csv" in summary_inputs[0]
        assert "read-origin" in summary_inputs[0]
        assert "sample_count=10" in summary_inputs[0]
