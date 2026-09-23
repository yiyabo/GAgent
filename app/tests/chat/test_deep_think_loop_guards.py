"""Loop-guard tests: deliverable acceptance, failure trap, no-progress endgame.

The guards only fire on pathological patterns; a healthy run must see zero
behaviour change (scenario C asserts exactly that).

Deliverable fixtures live under ./runtime/ rather than pytest's tmp_path:
production code deliberately ignores /tmp/ paths as scratch.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import DeepThinkAgent
from app.services.deep_think.acceptance import (
    extract_acceptance_spec,
    parse_acceptance_spec,
    spec_to_kind_requirements,
)
from app.services.deep_think.text_utils import (
    _missing_expectations,
    _missing_expectations_detailed,
)


SYNTH_ANSWER = "综合当前已收集的证据，本轮任务的关键结论如下：交付文件已生成并验证，可以直接使用。"

_SANDBOX = Path("runtime") / "test_loop_guards_sandbox"


async def _noop_tool_executor(_name: str, _params: dict):
    return {"success": True}


@pytest.fixture()
def deliverable_file():
    sandbox = _SANDBOX / "deliverables" / "latest" / "chart"
    sandbox.mkdir(parents=True, exist_ok=True)
    path = (sandbox / "overview.png").resolve()
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    yield path
    shutil.rmtree(_SANDBOX, ignore_errors=True)


class _LoopLLM:
    """Yields scripted native tool-call responses; records prompt messages."""

    def __init__(self, responses: list[NativeStreamResult]) -> None:
        self._responses = responses
        self._index = 0
        self.calls: list[list[dict]] = []
        self.chat_async_called = False

    async def stream_chat_with_tools_async(self, **kwargs):  # type: ignore[override]
        messages = kwargs.get("messages") or []
        self.calls.append(list(messages))
        if self._index >= len(self._responses):
            raise RuntimeError("No more scripted responses")
        value = self._responses[self._index]
        self._index += 1
        return value

    async def stream_chat_async(self, **kwargs):  # type: ignore[override]
        # synthesis path must stream (non-streaming gets 504'd by upstream)
        half = len(SYNTH_ANSWER) // 2
        yield SYNTH_ANSWER[:half]
        yield SYNTH_ANSWER[half:]

    async def chat_async(self, **kwargs):  # type: ignore[override]
        self.chat_async_called = True
        return SYNTH_ANSWER


def _tool_call_responses(tool: str, params: dict, count: int) -> list[NativeStreamResult]:
    return [
        NativeStreamResult(
            content=f"step {i}",
            tool_calls=[NativeToolCall(id=f"tc{i}", name=tool, arguments=dict(params))],
        )
        for i in range(count)
    ]


def _all_message_text(llm: _LoopLLM) -> str:
    parts: list[str] = []
    for messages in llm.calls:
        for msg in messages:
            parts.append(str(msg.get("content") or ""))
    return "\n".join(parts)


def test_no_progress_endgame_breaks_after_verified_deliverable(deliverable_file) -> None:
    """Deliverable verified at step 3, model never finishes -> nudge, then
    break far below max_iterations, then forced synthesis closes the run."""
    deliverable = deliverable_file

    call_count = {"n": 0}

    async def _executor(name: str, params: dict):
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return {"success": True, "artifact_paths": [str(deliverable)]}
        return {"success": True, "summary": "still probing, nothing new"}

    llm = _LoopLLM(
        _tool_call_responses("file_operations", {"operation": "read", "path": "/x"}, 40)
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_executor,
        max_iterations=40,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("create the overview figure"))

    # synthesis text is preserved and the produced image is inlined after it
    assert result.final_answer.startswith(SYNTH_ANSWER)
    assert "![overview.png](deliverables/latest/chart/overview.png)" in result.final_answer
    # synthesis must go through streaming — non-streaming gets 504'd upstream
    assert not llm.chat_async_called
    # 3 producing steps + break streak (default 14) -> must stop << 40
    assert result.total_iterations <= 18
    text = _all_message_text(llm)
    assert "verified on disk" in text
    assert str(deliverable) in text
    # the early break reason is surfaced on the step for the UI
    assert any(
        step.self_correction and "without new deliverables" in step.self_correction
        for step in result.thinking_steps
    )


def test_chat_turn_early_stops_on_substantive_answer_with_process_preface() -> None:
    """T1 scenario: single chat iteration, no tool calls; the content is a full
    substantive answer that happens to contain a process-sounding phrase
    ("我先…") mid-text. It must be served directly instead of being rejected as
    process-only and replaced by the minimal structured fallback."""
    content = (
        "可以分析。120 例（两组各 60）配基线与随访的 eGFR、肌酐、HbA1c，足以支撑两组肾功能变化对比。"
        "我建议分三步安排：数据体检（缺失/异常/单位判定）、组间统计比较、图表与研究报告。"
        "我先说明开工前需要你补充的两点：一是分组字段名与随访时间点，二是各指标单位。"
    )
    assert len(content) > 120
    llm = _LoopLLM([NativeStreamResult(content=content, tool_calls=[])])
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        max_iterations=5,
        request_profile={"request_tier": "standard", "intent_type": "chat"},
    )

    result = asyncio.run(agent.think("这个能分析吗？"))

    assert result.final_answer == content
    assert result.total_iterations == 1
    assert not result.fallback_used


def test_failure_signature_trap_warns_at_three_and_breaks_at_five() -> None:
    """Same failure five times -> one warning message, then an early break."""
    async def _executor(name: str, params: dict):
        return {
            "success": False,
            "error": "target_task_not_atomic",
            "summary": "code_executor can only execute atomic tasks",
        }

    llm = _LoopLLM(
        _tool_call_responses("code_executor", {"task_id": 3, "code": "print(1)"}, 30)
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_executor,
        max_iterations=30,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("run task 3"))

    # terminal tool errors route to the pre-existing blocked-tool answer,
    # which fires before forced synthesis — accept either close
    assert result.final_answer.strip()
    assert result.final_answer == SYNTH_ANSWER or "target_task_not_atomic" in result.final_answer
    # break at the 5th identical failure, not at the 30-iteration cap
    assert result.total_iterations <= 8
    # the warning is injected exactly once (it shows up in several recorded
    # snapshots only because message history accumulates across calls)
    per_snapshot_counts = [
        sum(
            1
            for msg in messages
            if "same failure has now occurred 3 times" in str(msg.get("content") or "")
        )
        for messages in llm.calls
    ]
    assert max(per_snapshot_counts) == 1
    assert sum(per_snapshot_counts) >= 1
    assert any(
        step.self_correction and "repetitions of the same failure" in step.self_correction
        for step in result.thinking_steps
    )


def test_healthy_run_sees_no_guard_nudges(deliverable_file) -> None:
    """Deliverable produced then submit_final_answer: zero guard interference."""
    deliverable = deliverable_file

    async def _executor(name: str, params: dict):
        return {"success": True, "artifact_paths": [str(deliverable)]}

    llm = _LoopLLM(
        [
            NativeStreamResult(
                content="make file",
                tool_calls=[
                    NativeToolCall(id="tc1", name="file_operations", arguments={"operation": "write"})
                ],
            ),
            NativeStreamResult(
                content="done",
                tool_calls=[
                    NativeToolCall(
                        id="final1",
                        name="submit_final_answer",
                        arguments={"answer": "done", "confidence": 0.9},
                    )
                ],
            ),
        ]
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_executor,
        max_iterations=10,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("create the figure"))

    # the submit path now inlines the produced image after the model's answer
    assert result.final_answer.startswith("done")
    assert "![overview.png](deliverables/latest/chart/overview.png)" in result.final_answer
    assert result.total_iterations <= 3
    text = _all_message_text(llm)
    assert "same failure" not in text
    assert "without producing any deliverable" not in text
    assert "No new deliverable" not in text


def test_deliverable_display_names_exclude_inputs_and_logs() -> None:
    from app.services.deep_think_agent import _collect_deliverable_display_names

    evidence = (
        "- 文件读取 (数据体检.md)：数据源：uploads/37d08f2f56cd______.xlsx ，工作表 分析用数据\n"
        "- 已写入文件：results/profile/data_audit_and_research_directions.md (9748 B)\n"
        "- 终端输出：run_20260919_072353_960198_3718d9d9_replace_log.txt written\n"
    )
    names = _collect_deliverable_display_names(evidence)
    assert names == ["data_audit_and_research_directions.md"]


def test_humanizer_strips_embedded_cli_protocol_json() -> None:
    agent = DeepThinkAgent(
        llm_client=_LoopLLM([]),
        available_tools=["code_executor"],
        tool_executor=_noop_tool_executor,
    )
    noisy_stdout = (
        '[{"type":"system","subtype":"init","uuid":"5f0d4824-cd69-568b-8706-799f58187604",'
        '"session_id":"5f0d4824","cwd":"/app/runtime/session_x","tools":["computer_use_bring_to_front"]}]'
        "\n"
        "analysis finished, 30 distinct rows"
    )
    humanized = agent._humanize_single_tool_result(
        "code_executor",
        {"success": True, "stdout": noisy_stdout, "artifact_paths": ["/app/runtime/s/results/out.csv"]},
    )
    assert "subtype" not in humanized
    assert "analysis finished" in humanized


def test_time_budget_breaks_long_tool_runs(monkeypatch) -> None:
    """Nested CLI tool calls take minutes each; the endgame must bound the run
    by wall-clock time, not iteration count."""
    # env floors (60s/120s) protect production; patch the helpers directly
    import app.services.deep_think_agent as dta

    monkeypatch.setattr(dta, "_time_budget_nudge_seconds", lambda: 1)
    monkeypatch.setattr(dta, "_time_budget_break_seconds", lambda: 2)

    async def _executor(name: str, params: dict):
        await asyncio.sleep(0.5)
        return {"success": True, "summary": "heavy nested cli run"}

    llm = _LoopLLM(
        _tool_call_responses("code_executor", {"task_id": 3, "code": "print(1)"}, 30)
    )
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["code_executor"],
        tool_executor=_executor,
        max_iterations=30,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("analyze the graph"))

    assert result.final_answer == SYNTH_ANSWER
    assert not llm.chat_async_called
    # 0.5s per cycle against a 2s budget: must break within a handful of cycles
    assert result.total_iterations <= 8
    text = _all_message_text(llm)
    assert "wall-clock time" in text
    assert any(
        step.self_correction and "wall-clock" in step.self_correction
        for step in result.thinking_steps
    )


class TestInlineImages:
    """_ensure_inline_images: produced images render inline in the reply."""

    def test_existing_inline_reference_kept(self) -> None:
        from app.services.deep_think_agent import _ensure_inline_images

        text = "见图：\n\n![饼图](deliverables/score_pie.png)\n\n说明。"
        out = _ensure_inline_images(text, ["deliverables/score_pie.png"])
        assert out == text

    def test_plain_link_upgraded(self) -> None:
        from app.services.deep_think_agent import _ensure_inline_images

        text = "结果见 [score_pie.png](deliverables/score_pie.png) 文件。"
        out = _ensure_inline_images(text, ["deliverables/score_pie.png"])
        assert "![score_pie.png](deliverables/score_pie.png)" in out

    def test_bare_filename_line_converted_at_its_position(self) -> None:
        from app.services.deep_think_agent import _ensure_inline_images

        text = "本轮已生成以下交付文件：\n- score_pie.png\n- report.csv\n\n如需调整请告诉我。"
        out = _ensure_inline_images(text, ["deliverables/score_pie.png"])
        assert "- ![score_pie.png](deliverables/score_pie.png)" in out
        # 图片出现在原来文件名的位置（report.csv 行之前），不是末尾
        assert out.index("![score_pie.png]") < out.index("- report.csv")
        assert out.rstrip().endswith("如需调整请告诉我。")

    def test_no_anchor_appends_as_last_resort(self) -> None:
        from app.services.deep_think_agent import _ensure_inline_images

        text = "分析完成，结论如下。"
        out = _ensure_inline_images(text, ["deliverables/score_pie.png"])
        assert out.startswith(text)
        assert "![score_pie.png](deliverables/score_pie.png)" in out

    def test_midline_mention_inserts_image_right_after_the_line(self) -> None:
        """The 2026-09-20 screenshot case: filename backticked inside a
        composite bullet — the image must appear right under that bullet, not
        at the end of the reply."""
        from app.services.deep_think_agent import _ensure_inline_images

        text = (
            "- 图表： `phage_trend_profile.png` / .pdf（6 面板，300 dpi）\n"
            "- 数据表： results/phage_trend/ 下 17 个 CSV/JSONL\n"
            "- 7 项主要产物已发布至 Deliverables"
        )
        out = _ensure_inline_images(text, ["deliverables/latest/image_tabular/phage_trend_profile.png"])
        lines = out.split("\n")
        mention_idx = next(i for i, line in enumerate(lines) if "图表" in line)
        image_idx = next(i for i, line in enumerate(lines) if line.startswith("![phage_trend_profile.png]"))
        table_idx = next(i for i, line in enumerate(lines) if "数据表" in line)
        assert mention_idx < image_idx < table_idx
        assert "![phage_trend_profile.png](deliverables/latest/image_tabular/phage_trend_profile.png)" in out

    def test_unsafe_and_missing_values_skipped(self) -> None:
        from app.services.deep_think_agent import _ensure_inline_images

        text = "done"
        out = _ensure_inline_images(text, ["", "../x.png", "a\\b.png", None])
        assert out == text

    def test_collect_relpaths_from_guard_mirror(self, monkeypatch) -> None:
        sandbox = _SANDBOX.resolve()
        session_dir = sandbox / "testsess" / "deliverables"
        session_dir.mkdir(parents=True, exist_ok=True)
        png = session_dir / "score_pie.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        csv = session_dir / "report.csv"
        csv.write_text("a,b\n1,2\n")
        monkeypatch.setenv("APP_RUNTIME_ROOT", str(sandbox))
        try:
            agent = DeepThinkAgent(
                llm_client=_LoopLLM([]),
                available_tools=[],
                tool_executor=_noop_tool_executor,
                max_iterations=1,
                request_profile={"session_id": "testsess"},
            )
            agent._produced_deliverable_paths = [
                str(png),
                str(csv),
                str(session_dir / "missing.png"),
                "raw_files/tmp/run/scratch.png",
            ]
            assert agent._collect_inline_image_relpaths() == ["deliverables/score_pie.png"]
        finally:
            shutil.rmtree(_SANDBOX, ignore_errors=True)

    def _guard_state(self) -> dict:
        import time

        return {
            "verified_deliverables": [],
            "failure_sig_counts": {},
            "failure_sig_warned": set(),
            "last_progress_iteration": 0,
            "no_progress_nudge_sent": False,
            "time_nudge_sent": False,
            "started_at": time.monotonic(),
            "expected_outputs": [],
            "acceptance_spec": None,
        }

    def _image_agent(self) -> DeepThinkAgent:
        return DeepThinkAgent(
            llm_client=_LoopLLM([]),
            available_tools=[],
            tool_executor=_noop_tool_executor,
            max_iterations=1,
            request_profile={"session_id": "testsess"},
        )

    def test_image_collection_widens_without_touching_progress(self, monkeypatch) -> None:
        """raw_files/tmp images join the inline mirror; verified_deliverables
        (loop-guard progress) stays exactly as the un-widened guard sees it."""
        from app.services.deep_think.guards import _apply_loop_guards

        sandbox = _SANDBOX.resolve()
        tmp_img = sandbox / "testsess" / "raw_files" / "tmp" / "run1"
        tmp_img.mkdir(parents=True, exist_ok=True)
        raw_png = tmp_img / "fig.png"
        raw_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        deliv_dir = sandbox / "testsess" / "deliverables"
        deliv_dir.mkdir(parents=True, exist_ok=True)
        deliv_png = deliv_dir / "chart.png"
        deliv_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        monkeypatch.setenv("APP_RUNTIME_ROOT", str(sandbox))
        try:
            agent = self._image_agent()
            state = self._guard_state()
            tool_results = [
                {"tool_result": {"success": True, "artifact_paths": [str(raw_png), str(deliv_png)]}}
            ]
            _apply_loop_guards(
                agent,
                messages=[],
                tool_results=tool_results,
                iteration=1,
                guard_state=state,
            )
            produced_images = getattr(agent, "_produced_image_paths", None) or []
            assert str(raw_png) in produced_images
            assert str(deliv_png) in produced_images
            # deliverable progress semantics unchanged: only the deliverables/
            # image counts as progress; the raw_files one stays out
            assert state["verified_deliverables"] == [str(deliv_png)]
            assert (getattr(agent, "_produced_deliverable_paths", None) or []) == [str(deliv_png)]
        finally:
            shutil.rmtree(_SANDBOX, ignore_errors=True)

    def test_collect_relpaths_from_image_mirror_widened_segments(self, monkeypatch) -> None:
        sandbox = _SANDBOX.resolve()
        fig_dir = sandbox / "testsess" / "figures"
        fig_dir.mkdir(parents=True, exist_ok=True)
        fig_png = fig_dir / "plot.png"
        fig_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        tmp_dir = sandbox / "testsess" / "raw_files" / "tmp" / "run1"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_png = tmp_dir / "fig.png"
        tmp_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        monkeypatch.setenv("APP_RUNTIME_ROOT", str(sandbox))
        try:
            agent = self._image_agent()
            agent._produced_image_paths = [str(fig_png), str(tmp_png)]
            assert agent._collect_inline_image_relpaths() == [
                "figures/plot.png",
                "raw_files/tmp/run1/fig.png",
            ]
        finally:
            shutil.rmtree(_SANDBOX, ignore_errors=True)

    def test_image_collection_still_excludes_scratch(self, monkeypatch) -> None:
        from app.services.deep_think.guards import _apply_loop_guards

        sandbox = _SANDBOX.resolve()
        probe_dir = sandbox / "testsess" / "tool_outputs"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe_png = probe_dir / "probe.png"
        probe_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        up_dir = sandbox / "testsess" / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        up_png = up_dir / "up.png"
        up_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        monkeypatch.setenv("APP_RUNTIME_ROOT", str(sandbox))
        try:
            agent = self._image_agent()
            state = self._guard_state()
            tool_results = [
                {"tool_result": {"success": True, "artifact_paths": [str(probe_png), str(up_png)]}}
            ]
            _apply_loop_guards(
                agent,
                messages=[],
                tool_results=tool_results,
                iteration=1,
                guard_state=state,
            )
            assert (getattr(agent, "_produced_image_paths", None) or []) == []
            assert state["verified_deliverables"] == []
        finally:
            shutil.rmtree(_SANDBOX, ignore_errors=True)


class TestDeclarativeAcceptance:
    """expected_outputs: the guard judges completion, not just pathologies."""

    def test_derive_expected_outputs(self) -> None:
        from app.services.deep_think_agent import _derive_expected_outputs, _missing_expectations

        assert _derive_expected_outputs("画一张得分饼图") == ["image"]
        assert _derive_expected_outputs("把结果导出成 csv") == ["data"]
        assert _derive_expected_outputs("写一份 markdown 报告") == ["document"]
        assert _derive_expected_outputs("plot a chart and save the csv") == ["image", "data"]
        assert _derive_expected_outputs("分析一下这些数据说明了什么") == []
        assert _missing_expectations(["image", "data"], ["/x/a.png"]) == ["data"]
        assert _missing_expectations(["image"], ["/x/a.png"]) == []
        assert _missing_expectations([], []) == []

    def test_extension_granted_then_break_with_gaps(self) -> None:
        """Asked for a figure but only a csv gets produced: the guard must not
        break at the normal streak — it extends once, then closes with the gap
        recorded for the final answer."""
        csv_dir = _SANDBOX / "deliverables" / "latest"
        csv_dir.mkdir(parents=True, exist_ok=True)
        csv_path = (csv_dir / "scores.csv").resolve()
        csv_path.write_text("name,score\na,1\n")

        async def _executor(name: str, params: dict):
            return {"success": True, "artifact_paths": [str(csv_path)]}

        # vary params per cycle so the identical-cycle breaker stays out of the
        # way and the no-progress path exercises the acceptance extension
        responses = [
            NativeStreamResult(
                content=f"step {i}",
                tool_calls=[
                    NativeToolCall(
                        id=f"tc{i}",
                        name="file_operations",
                        arguments={"operation": "read", "path": f"/x/{i}"},
                    )
                ],
            )
            for i in range(40)
        ]
        llm = _LoopLLM(responses)
        agent = DeepThinkAgent(
            llm_client=llm,
            available_tools=["file_operations"],
            tool_executor=_executor,
            max_iterations=40,
            request_profile={"request_tier": "execute", "intent_type": "execute_task"},
        )
        try:
            result = asyncio.run(agent.think("画一张得分饼图"))
        finally:
            shutil.rmtree(_SANDBOX, ignore_errors=True)

        # extension: survived past the normal break streak, then closed shortly after
        assert result.total_iterations >= 15
        assert result.total_iterations <= 22
        text = _all_message_text(llm)
        assert "required deliverable type(s) still missing: image" in text
        assert agent._acceptance_missing == ["image"]
        assert any(
            step.self_correction and "without new deliverables" in step.self_correction
            for step in result.thinking_steps
        )

    def test_satisfied_expectations_see_no_extension(self, deliverable_file) -> None:
        """Asked for a figure and it IS produced: normal break, zero gap."""

        async def _executor(name: str, params: dict):
            return {"success": True, "artifact_paths": [str(deliverable_file)]}

        llm = _LoopLLM(
            _tool_call_responses("file_operations", {"operation": "read", "path": "/x"}, 40)
        )
        agent = DeepThinkAgent(
            llm_client=llm,
            available_tools=["file_operations"],
            tool_executor=_executor,
            max_iterations=40,
            request_profile={"request_tier": "execute", "intent_type": "execute_task"},
        )
        result = asyncio.run(agent.think("画一张得分饼图"))

        assert result.total_iterations <= 16
        assert agent._acceptance_missing == []
        text = _all_message_text(llm)
        assert "required deliverable type(s) still missing" not in text


# ---------------------------------------------------------------------------
# Declarative acceptance v2: LLM spec extraction (opt-in) + count-aware guard
# ---------------------------------------------------------------------------

_SPEC_JSON = (
    '{"required_outputs": ['
    '{"kind": "image", "min_count": 2, "extensions": [".png"], '
    '"constraints": "CJK axis labels", "in_place": false},'
    '{"kind": "document", "min_count": 1, "extensions": [".md"], '
    '"target_path": "deliverables/report.md"}'
    ']}'
)


class _SpecExtractLLM:
    """Fake client for the v2 extraction call (stream_chat_async)."""

    def __init__(self, chunks=None, exc: Exception | None = None) -> None:
        self.calls = 0
        self._chunks = list(chunks or [])
        self._exc = exc

    async def stream_chat_async(self, prompt: str = "", **kwargs):  # type: ignore[override]
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        for chunk in self._chunks:
            yield chunk


def _spec_agent(llm, tier: str = "execute"):
    from types import SimpleNamespace

    return SimpleNamespace(llm_client=llm, _request_tier=lambda: tier)


class TestAcceptanceV2Parser:
    def test_full_spec_parsed(self) -> None:
        spec = parse_acceptance_spec(f"```json\n{_SPEC_JSON}\n```")
        assert spec is not None
        image, document = spec.required_outputs
        assert image.kind == "image"
        assert image.min_count == 2
        assert image.extensions == [".png"]
        assert image.constraints == "CJK axis labels"
        assert document.kind == "document"
        assert document.target_path == "deliverables/report.md"
        assert spec_to_kind_requirements(spec) == {"image": 2, "document": 1}

    def test_invalid_json_returns_none(self) -> None:
        assert parse_acceptance_spec("not json at all") is None
        assert parse_acceptance_spec('{"required_outputs": "nope"}') is None
        assert parse_acceptance_spec("") is None

    def test_normalization_rules(self) -> None:
        spec = parse_acceptance_spec(
            '{"required_outputs": ['
            '{"kind": "figure", "min_count": 99, "extensions": ["png", ".exe", ".md"],'
            ' "target_path": "../escape.md"},'
            '{"kind": "hologram", "min_count": 1}'
            ']}'
        )
        assert spec is not None
        image, other = spec.required_outputs
        assert image.kind == "image"  # alias normalized
        assert image.min_count == 10  # clamped
        assert image.extensions == [".png", ".md"]  # dotted, filtered
        assert image.target_path is None  # traversal rejected
        assert other.kind == "other"
        # "other" kinds never drive deterministic blocking checks
        assert spec_to_kind_requirements(spec) == {"image": 10}


class TestAcceptanceV2Extraction:
    def test_disabled_env_skips_llm(self, monkeypatch) -> None:
        monkeypatch.delenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", raising=False)
        llm = _SpecExtractLLM(chunks=[_SPEC_JSON])
        spec = asyncio.run(extract_acceptance_spec(_spec_agent(llm), "画两张图并写报告"))
        assert spec is None
        assert llm.calls == 0

    def test_non_allowed_tier_skips_llm(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
        llm = _SpecExtractLLM(chunks=[_SPEC_JSON])
        spec = asyncio.run(extract_acceptance_spec(_spec_agent(llm, tier="standard"), "画两张图并写报告"))
        assert spec is None
        assert llm.calls == 0

    def test_execute_tier_extracts_spec(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
        llm = _SpecExtractLLM(chunks=[_SPEC_JSON[:60], _SPEC_JSON[60:]])
        spec = asyncio.run(extract_acceptance_spec(_spec_agent(llm), "画两张图并写报告"))
        assert spec is not None
        assert llm.calls == 1
        assert spec_to_kind_requirements(spec) == {"image": 2, "document": 1}

    def test_garbage_and_error_fall_back_silently(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
        garbage = _SpecExtractLLM(chunks=["sorry, cannot help"])
        assert asyncio.run(extract_acceptance_spec(_spec_agent(garbage), "画两张图")) is None
        failing = _SpecExtractLLM(exc=RuntimeError("upstream 504"))
        assert asyncio.run(extract_acceptance_spec(_spec_agent(failing), "画两张图")) is None


class TestAcceptanceV2MissingCounts:
    def test_no_requirements_matches_v1(self) -> None:
        expected = ["image", "document"]
        paths = ["deliverables/latest/chart/a.png"]
        assert _missing_expectations_detailed(expected, paths, None) == _missing_expectations(expected, paths)

    def test_count_shortfall_rendered(self) -> None:
        paths = ["deliverables/latest/chart/a.png"]
        missing = _missing_expectations_detailed([], paths, {"image": 2, "document": 1})
        assert missing == ["imagex1", "document"]
        assert _missing_expectations_detailed([], paths + ["b.png", "r.md"], {"image": 2, "document": 1}) == []

    def test_v1_kinds_outside_spec_still_reported(self) -> None:
        missing = _missing_expectations_detailed(["image", "data"], ["a.png"], {"image": 1})
        assert missing == ["data"]


class _SpecLoopLLM(_LoopLLM):
    """Loop LLM that also answers the v2 extraction call with valid spec JSON."""

    def __init__(self, responses, spec_chunks) -> None:
        super().__init__(responses)
        self.spec_calls = 0
        self._spec_chunks = list(spec_chunks)

    async def stream_chat_async(self, prompt: str = "", **kwargs):  # type: ignore[override]
        self.spec_calls += 1
        for chunk in self._spec_chunks:
            yield chunk


def _submit_final_responses() -> list[NativeStreamResult]:
    return [
        NativeStreamResult(
            content="done",
            tool_calls=[
                NativeToolCall(
                    id="tc0",
                    name="submit_final_answer",
                    arguments={"answer": "完成：两张图与报告均已产出。", "confidence": 0.9},
                )
            ],
        )
    ]


def test_acceptance_v2_loop_extracts_spec_and_injects_prompt(monkeypatch) -> None:
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    llm = _SpecLoopLLM(_submit_final_responses(), [_SPEC_JSON])
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        max_iterations=5,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("画两张柱状图并写一份研究报告"))

    assert result.final_answer.startswith("完成")
    assert llm.spec_calls == 1
    assert agent._acceptance_spec is not None
    assert spec_to_kind_requirements(agent._acceptance_spec) == {"image": 2, "document": 1}
    system_prompt = llm.calls[0][0]["content"]
    assert "DELIVERABLE SPEC (acceptance v2)" in system_prompt
    assert "image x2" in system_prompt
    assert "CJK axis labels" in system_prompt


def test_acceptance_v2_disabled_loop_keeps_v1_only(monkeypatch) -> None:
    monkeypatch.delenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", raising=False)
    llm = _SpecLoopLLM(_submit_final_responses(), [_SPEC_JSON])
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        max_iterations=5,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("画两张柱状图并写一份研究报告"))

    assert result.final_answer.startswith("完成")
    assert llm.spec_calls == 0
    assert agent._acceptance_spec is None
    assert "DELIVERABLE SPEC" not in llm.calls[0][0]["content"]


def test_acceptance_v2_invalid_spec_loop_falls_back_to_v1(monkeypatch) -> None:
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    llm = _SpecLoopLLM(_submit_final_responses(), ["no json here"])
    agent = DeepThinkAgent(
        llm_client=llm,
        available_tools=["file_operations"],
        tool_executor=_noop_tool_executor,
        max_iterations=5,
        request_profile={"request_tier": "execute", "intent_type": "execute_task"},
    )

    result = asyncio.run(agent.think("画两张柱状图并写一份研究报告"))

    assert result.final_answer.startswith("完成")
    assert agent._acceptance_spec is None
    assert "DELIVERABLE SPEC" not in llm.calls[0][0]["content"]
