"""p75 native in-process parallel sub-agent engine.

Covers the ``NativeSubagentExecutor`` contract (roles, tool clamps, recursion
guard, workspace isolation, usage attribution) and the ``delegate_task``
native backend (single child, parallel ``tasks`` fan-out with real overlap and
per-child failure isolation), the Plan-mode scout-only gate, and the
RunInspector ``child_runs`` projection.  A fake agent stands in for
``DeepThinkAgent`` so no LLM is called.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from app.llm import _usage_context as _llm_usage_ctx
from app.services.deep_think.plan_mode import plan_mode_denial, plan_mode_tool_allowed
from app.services.plans import native_subagent as ns
from app.services.plans.native_subagent import (
    FORBIDDEN_CHILD_TOOLS,
    NativeSubagentExecutor,
    ParentContext,
    SubagentSpec,
    clamp_violation,
    role_tools,
)
from tool_box.context import ToolContext
from tool_box.tools_impl import delegate_task as dt


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    _llm_usage_ctx.set(None)
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    monkeypatch.delenv("DELEGATE_TASK_BACKEND", raising=False)
    monkeypatch.delenv("DELEGATE_TASK_MAX_ITERATIONS", raising=False)
    monkeypatch.delenv("DELEGATE_TASK_TIMEOUT_SECONDS", raising=False)
    yield
    _llm_usage_ctx.set(None)


class _Outcome:
    def __init__(self, answer: str, iterations: int = 2, termination: str = "final_answer") -> None:
        self.final_answer = answer
        self.total_iterations = iterations
        self.termination_reason = termination


class _FakeFactory:
    """Records constructor kwargs; ``behaviour(goal, agent_kwargs)`` drives think()."""

    def __init__(self, behaviour=None) -> None:
        self.created: list[dict[str, Any]] = []
        self.windows: list[tuple[float, float]] = []
        self.prompts: list[str] = []
        self.contexts: list[dict[str, Any]] = []
        self._behaviour = behaviour

    def __call__(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        factory = self

        class _Agent:
            async def think(self, prompt: str, context: dict[str, Any] | None = None) -> _Outcome:
                factory.prompts.append(prompt)
                factory.contexts.append(dict(context or {}))
                start = time.perf_counter()
                try:
                    if factory._behaviour is not None:
                        return await factory._behaviour(prompt, kwargs)
                    await asyncio.sleep(0.05)
                    return _Outcome("done: " + prompt.splitlines()[3])
                finally:
                    factory.windows.append((start, time.perf_counter()))

        return _Agent()


def _parent(tmp_path: Path, **overrides: Any) -> ParentContext:
    values: dict[str, Any] = {"work_dir": str(tmp_path), "job_id": "job_parent_1"}
    values.update(overrides)
    return ParentContext(**values)


# --- role tool surfaces & clamps --------------------------------------------------


@pytest.mark.parametrize("role", ["scout", "researcher", "worker", "verifier"])
def test_no_role_can_recurse_or_submit(role: str) -> None:
    tools = role_tools(role)
    assert tools
    for forbidden in ("delegate_task", "code_executor", "plan_operation", "deliverable_submit"):
        assert forbidden in FORBIDDEN_CHILD_TOOLS
        assert forbidden not in tools


@pytest.mark.parametrize("sandbox", ["0", "1"])
def test_role_tool_surfaces(monkeypatch: pytest.MonkeyPatch, sandbox: str) -> None:
    monkeypatch.setenv("CODE_MODE_SANDBOX", sandbox)
    local = {"file_operations", "document_reader"}
    for role in ("scout", "worker", "researcher"):
        assert set(role_tools(role)) == local
    assert set(role_tools("verifier")) == local | {"vision_reader"}
    assert role_tools("unknown") == []


@pytest.mark.parametrize("operation", ["write", "delete", "move", "copy", "mkdir", ""])
def test_scout_file_operations_writes_are_refused(operation: str) -> None:
    refusal = clamp_violation("scout", "file_operations", {"operation": operation, "path": "/x"})
    assert refusal is not None
    assert refusal["success"] is False
    assert refusal["error"] == "subagent_read_only"


@pytest.mark.parametrize("operation", ["read", "list", "exists", "info"])
def test_scout_file_operations_reads_pass(operation: str) -> None:
    assert clamp_violation("scout", "file_operations", {"operation": operation}) is None


def test_verifier_clamp_allows_profile_but_not_write() -> None:
    assert clamp_violation("verifier", "file_operations", {"operation": "profile"}) is None
    assert clamp_violation("verifier", "file_operations", {"operation": "census"}) is None
    assert clamp_violation("verifier", "file_operations", {"operation": "write"}) is not None
    # Worker keeps full file access.
    assert clamp_violation("worker", "file_operations", {"operation": "write"}) is None


def test_off_surface_tool_is_refused() -> None:
    refusal = clamp_violation("scout", "execute_code", {"code": "print(1)"})
    assert refusal is not None and refusal["error"] == "subagent_tool_not_allowed"
    assert clamp_violation("worker", "delegate_task", {"goal": "x"}) is not None


async def test_scout_write_is_clamped_before_reaching_the_tool(tmp_path: Path) -> None:
    executed: list[tuple[str, dict[str, Any]]] = []

    async def tool_executor(name: str, params: dict[str, Any], ctx: Any) -> dict[str, Any]:
        executed.append((name, params))
        return {"success": True, "work_dir": ctx.work_dir, "channel": ctx.channel}

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        run = kwargs["tool_executor"]
        write = await run("file_operations", {"operation": "write", "path": "a.txt", "content": "x"})
        read = await run("file_operations", {"operation": "read", "path": "a.txt"})
        assert write["error"] == "subagent_read_only"
        assert read["success"] is True and read["channel"] == "subagent"
        return _Outcome("ok")

    executor = NativeSubagentExecutor(agent_factory=_FakeFactory(behaviour), tool_executor=tool_executor)
    [result] = await executor.run_many([SubagentSpec(goal="look", role="scout")], _parent(tmp_path))

    assert result["status"] == "completed"
    expected = tmp_path / "subagents" / result["subrun_id"] / "a.txt"
    assert executed == [("file_operations", {"operation": "read", "path": str(expected)})]


# --- single child ------------------------------------------------------------------


@pytest.mark.parametrize(("role", "iterations"), [("scout", 10), ("worker", 14)])
async def test_single_child_contract(tmp_path: Path, role: str, iterations: int) -> None:
    factory = _FakeFactory()
    progress: list[dict[str, Any]] = []

    async def on_progress(event: dict[str, Any]) -> None:
        progress.append(event)

    executor = NativeSubagentExecutor(agent_factory=factory)
    input_dir = tmp_path / "notes"
    input_dir.mkdir()
    spec = SubagentSpec(goal="Summarize inputs", role=role, deliverable="a memo", context_paths=[str(input_dir)])
    [result] = await executor.run_many([spec], _parent(tmp_path, on_progress=on_progress))

    assert set(result) == {
        "subrun_id",
        "child_run_id",
        "parent_run_id",
        "role",
        "goal",
        "deliverable",
        "status",
        "iterations",
        "summary",
        "artifact_paths",
        "workspace",
        "duration_ms",
        "error",
    }
    assert result["status"] == "completed"
    assert result["role"] == role
    assert result["summary"] == "done: Summarize inputs"
    assert result["error"] is None
    kwargs = factory.created[0]
    assert kwargs["available_tools"] == role_tools(role)
    assert kwargs["max_iterations"] == iterations
    assert isinstance(kwargs["cancel_event"], asyncio.Event)
    assert result["parent_run_id"] == "job_parent_1"
    assert result["child_run_id"] == f"job_parent_1:sub_{result['subrun_id']}"
    # Isolated workspace under the parent work dir; context paths reach the prompt.
    # The child produced nothing, so its empty scratch workspace is removed.
    sub_dir = tmp_path / "subagents" / result["subrun_id"]
    assert result["workspace"] is None
    assert not sub_dir.exists()
    assert str(sub_dir) in factory.prompts[0]
    assert str(input_dir) in factory.prompts[0]
    assert "a memo" in factory.prompts[0]
    assert factory.contexts[0]["readable_dirs"] == [str(input_dir)]
    assert [e["status"] for e in progress] == ["started", "completed"]
    assert [e["stage"] for e in progress] == ["started", "completed"]
    assert all(e["subagent_id"] == result["subrun_id"] and e["role"] == role for e in progress)
    assert all(e["child_run_id"] == result["child_run_id"] and e["message"] for e in progress)


async def test_session_workspace_lives_under_runtime_session_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.session_paths.get_runtime_session_dir",
        lambda session_id, **_: tmp_path / "runtime" / f"session_{session_id}",
    )
    [result] = await NativeSubagentExecutor(agent_factory=_FakeFactory()).run_many(
        [SubagentSpec(goal="g")], ParentContext(session_id="abc")
    )
    container = tmp_path / "runtime" / "session_abc" / "subagents"
    assert container.is_dir()
    assert result["workspace"] is None
    assert not (container / result["subrun_id"]).exists()


async def test_worker_reads_selected_input_and_writes_to_its_own_workspace(tmp_path, monkeypatch) -> None:
    from app.services import path_router
    from app.services.session_paths import get_runtime_session_dir
    from tool_box.tools_impl import file_operations

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(path_router, "_default_router", None)
    monkeypatch.setattr(file_operations, "_get_project_root", lambda: tmp_path.resolve())
    session_id = "session_p75_io"
    session_dir = get_runtime_session_dir(session_id, create=True).resolve()
    source = session_dir / "notes.txt"
    source.write_text("input note", encoding="utf-8")

    async def read_and_write(prompt, kwargs):
        run = kwargs["tool_executor"]
        read = await run("file_operations", {"operation": "read", "path": str(source)})
        assert read["success"], read
        write = await run("file_operations", {"operation": "write", "path": "report.txt", "content": read["content"]})
        assert write["success"], write
        denied = await run("file_operations", {"operation": "write", "path": str(source), "content": "changed"})
        assert denied["executed"] is False
        return _Outcome("reported")

    executor = NativeSubagentExecutor(agent_factory=_FakeFactory(read_and_write))
    [result] = await executor.run_many(
        [SubagentSpec(goal="copy note", context_paths=[str(source)])], ParentContext(session_id=session_id)
    )
    assert result["status"] == "completed", result
    expected = session_dir / "subagents" / result["subrun_id"] / "report.txt"
    assert result["artifact_paths"] == [str(expected)]
    assert result["workspace"] == str(expected.parent)
    assert expected.read_text(encoding="utf-8") == source.read_text(encoding="utf-8") == "input note"


async def test_max_iterations_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DELEGATE_TASK_MAX_ITERATIONS", "3")
    factory = _FakeFactory()
    await NativeSubagentExecutor(agent_factory=factory).run_many([SubagentSpec(goal="g")], _parent(tmp_path))
    assert factory.created[0]["max_iterations"] == 3


async def test_child_timeout_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DELEGATE_TASK_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setattr(ns, "CANCEL_GRACE_SECONDS", 0.05)

    async def slow(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        await asyncio.sleep(5)
        return _Outcome("never")

    [result] = await NativeSubagentExecutor(agent_factory=_FakeFactory(slow)).run_many(
        [SubagentSpec(goal="g")], _parent(tmp_path)
    )
    assert result["status"] == "failed"
    assert result["error"] == "subagent_timeout"


async def test_parent_cancel_propagates_to_child_cancel_event(tmp_path: Path) -> None:
    parent_cancel = asyncio.Event()

    async def wait_cancel(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        parent_cancel.set()
        await asyncio.wait_for(kwargs["cancel_event"].wait(), timeout=2)
        return _Outcome("", termination="cancelled")

    [result] = await NativeSubagentExecutor(agent_factory=_FakeFactory(wait_cancel)).run_many(
        [SubagentSpec(goal="g")], _parent(tmp_path, parent_cancel=parent_cancel)
    )
    assert result["status"] == "cancelled"


# --- recursion guard ---------------------------------------------------------------


async def test_recursion_guard_rejects_deep_chains(tmp_path: Path) -> None:
    factory = _FakeFactory()
    [result] = await NativeSubagentExecutor(agent_factory=factory).run_many(
        [SubagentSpec(goal="g")], _parent(tmp_path, delegation_depth=1)
    )
    assert result["status"] == "failed"
    assert result["error"] == "subagent_recursion_limit"
    assert factory.created == []


async def test_recursion_guard_reaches_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _FakeFactory()
    monkeypatch.setattr(dt, "_new_native_executor", lambda: NativeSubagentExecutor(agent_factory=factory))
    ctx = ToolContext(work_dir=str(tmp_path), extra={"delegation_depth": 1})
    result = await dt.delegate_task_handler(goal="g", tool_context=ctx)
    assert result["success"] is False
    assert result["subagent_runs"][0]["error"] == "subagent_recursion_limit"
    assert factory.created == []


async def test_deep_plan_tree_ancestor_chain_does_not_block_first_delegation(tmp_path: Path) -> None:
    factory = _FakeFactory()
    [result] = await NativeSubagentExecutor(agent_factory=factory).run_many(
        [SubagentSpec(goal="g", role="scout")],
        _parent(tmp_path, ancestor_chain=[1, 5, 12], delegation_depth=0),
    )
    assert result["status"] == "completed"
    assert len(factory.created) == 1

# --- parallel fan-out --------------------------------------------------------------


async def test_parallel_fan_out_overlaps_and_isolates_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        await asyncio.sleep(0.2)
        if "explode" in prompt:
            raise RuntimeError("child boom")
        return _Outcome("finished " + prompt.splitlines()[3])

    factory = _FakeFactory(behaviour)
    monkeypatch.setattr(dt, "_new_native_executor", lambda: NativeSubagentExecutor(agent_factory=factory))
    progress: list[dict[str, Any]] = []

    async def on_progress(event: dict[str, Any]) -> None:
        progress.append(event)

    ctx = ToolContext(work_dir=str(tmp_path), job_id="job_fan", on_progress=on_progress)
    started = time.perf_counter()
    result = await dt.delegate_task_handler(
        tasks=[
            {"goal": "scan A", "role": "scout"},
            {"goal": "explode B", "role": "worker"},
            {"goal": "check C", "role": "verifier", "deliverable": "verdict"},
        ],
        tool_context=ctx,
    )
    elapsed = time.perf_counter() - started

    # Real concurrency: three 0.2s children finish well under the serial 0.6s.
    assert elapsed < 0.5
    starts = [w[0] for w in factory.windows]
    ends = [w[1] for w in factory.windows]
    assert max(starts) < min(ends)

    runs = result["subagent_runs"]
    assert [r["role"] for r in runs] == ["scout", "worker", "verifier"]
    assert [r["status"] for r in runs] == ["completed", "failed", "completed"]
    assert runs[1]["error"] == "subagent_error"
    assert "child boom" in runs[1]["summary"]
    assert result["status"] == "partial"
    assert result["success"] is True
    assert result["trace_ref"]["backend"] == "native"
    assert len({r["subrun_id"] for r in runs}) == 3
    assert {e["status"] for e in progress} == {"started", "completed", "failed"}


async def test_fan_out_respects_semaphore(tmp_path: Path) -> None:
    active = 0
    peak = 0

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return _Outcome("ok")

    executor = NativeSubagentExecutor(agent_factory=_FakeFactory(behaviour), max_concurrency=4)
    runs = await executor.run_many([SubagentSpec(goal=f"g{i}") for i in range(6)], _parent(tmp_path))
    assert len(runs) == 6
    assert peak == 4


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({}, "empty_goal"),
        ({"goal": "  ", "tasks": []}, "empty_goal"),
        ({"goal": "a", "tasks": [{"goal": "b"}]}, "goal_and_tasks"),
        ({"tasks": [{"goal": "x"}] * 7}, "invalid_tasks"),
        ({"tasks": [{"role": "scout"}]}, "invalid_tasks"),
        ({"goal": "a", "role": "boss"}, "invalid_role"),
    ],
)
async def test_handler_validation(kwargs: dict[str, Any], error: str) -> None:
    result = await dt.delegate_task_handler(**kwargs)
    assert result["success"] is False
    assert result["error"] == error


def test_native_backend_is_the_default_and_concurrent_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    from tool_box.tool_registry import _dynamic_concurrent_safe

    assert dt.delegate_task_backend() == "native"
    assert _dynamic_concurrent_safe("delegate_task") is True
    monkeypatch.setenv("DELEGATE_TASK_BACKEND", "cli")
    assert dt.delegate_task_backend() == "cli"
    assert _dynamic_concurrent_safe("delegate_task") is False


# --- Plan mode ---------------------------------------------------------------------


def test_plan_mode_allows_only_scout_delegation() -> None:
    assert plan_mode_tool_allowed("delegate_task", {"goal": "g", "role": "scout"}) is True
    assert (
        plan_mode_tool_allowed(
            "delegate_task", {"tasks": [{"goal": "a", "role": "scout"}, {"goal": "b", "role": "scout"}]}
        )
        is True
    )
    assert plan_mode_tool_allowed("delegate_task", {"goal": "g", "role": "worker"}) is False
    # Default role is worker -> blocked.
    assert plan_mode_tool_allowed("delegate_task", {"goal": "g"}) is False
    assert (
        plan_mode_tool_allowed(
            "delegate_task", {"tasks": [{"goal": "a", "role": "scout"}, {"goal": "b", "role": "researcher"}]}
        )
        is False
    )


def test_plan_mode_denial_hints_scout() -> None:
    denial = plan_mode_denial("delegate_task", {"goal": "g", "role": "worker"})
    assert denial["error"] == "plan_mode_read_only"
    assert 'role="scout"' in denial["hint"]
    assert "worker" in denial["summary"]


# --- usage attribution & RunInspector ----------------------------------------------


@pytest.fixture
def ledger_db(tmp_path: Path):
    from app.database_pool import close_connection_pool, initialize_connection_pool
    from app.repository.llm_usage import init_llm_usage_table

    initialize_connection_pool(db_path=str(tmp_path / "ledger.db"))
    init_llm_usage_table()
    try:
        yield
    finally:
        close_connection_pool()


async def test_usage_rows_carry_parent_run_id_and_surface_in_inspector(tmp_path: Path, ledger_db: None) -> None:
    from app.llm import _log_usage, current_usage_context
    from app.services.run_inspector import build_run_inspection, load_child_runs

    seen_contexts: list[dict[str, Any]] = []

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        seen_contexts.append(dict(current_usage_context()))
        _log_usage("qwen", "qwen3.8-flash", 100, 20, 120, call_status="ok", duration_ms=50.0)
        return _Outcome("ok")

    runs = await NativeSubagentExecutor(agent_factory=_FakeFactory(behaviour)).run_many(
        [SubagentSpec(goal="a", role="scout"), SubagentSpec(goal="b", role="worker")],
        _parent(tmp_path, job_id="chat_run_p75"),
    )
    # Attribution is scoped to the child call and cleared afterwards.
    assert current_usage_context() in ({}, None) or not current_usage_context().get("run_id")
    assert {c["call_purpose"] for c in seen_contexts} == {"subagent_scout", "subagent_worker"}
    assert all(c["phase"] == "subagent" for c in seen_contexts)

    children = load_child_runs("chat_run_p75")
    expected_ids = {f"chat_run_p75:sub_{run['subrun_id']}" for run in runs}
    assert {c["child_run_id"] for c in children} == expected_ids
    assert all(c["call_count"] == 1 and c["total_tokens"] == 120 for c in children)
    assert sorted(p for c in children for p in c["call_purposes"]) == ["subagent_scout", "subagent_worker"]
    assert load_child_runs("no_such_parent") == []

    report = build_run_inspection({"run_id": "chat_run_p75", "status": "succeeded"}, [], {}, [], child_runs=children)
    assert report["child_runs"] == children
    assert build_run_inspection({"run_id": "r"}, [], {}, [])["child_runs"] == []


def test_children_never_create_chat_runs_rows() -> None:
    source = Path(ns.__file__).read_text(encoding="utf-8").lower()
    assert "insert into chat_runs" not in source
    assert "create_run" not in source
    assert "chat_run_state import chat_run_claim" in source



def test_process_wide_limiter_caps_concurrency_across_event_loops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import concurrent.futures
    import threading

    monkeypatch.setenv("DELEGATE_TASK_MAX_CONCURRENCY", "2")
    ns._PROCESS_LIMITERS.clear()
    lock = threading.Lock()
    active = 0
    peak = 0

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            await asyncio.sleep(0.12)
            return _Outcome("ok")
        finally:
            with lock:
                active -= 1

    def _worker(idx: int) -> list[dict[str, Any]]:
        sub_tmp = tmp_path / f"loop_{idx}"
        sub_tmp.mkdir(parents=True, exist_ok=True)
        executor = NativeSubagentExecutor(agent_factory=_FakeFactory(behaviour))
        return asyncio.run(
            executor.run_many(
                [SubagentSpec(goal=f"g{idx}_a", role="scout"), SubagentSpec(goal=f"g{idx}_b", role="scout")],
                _parent(sub_tmp, job_id=f"job_{idx}"),
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_worker, 1)
        f2 = pool.submit(_worker, 2)
        r1 = f1.result(timeout=10)
        r2 = f2.result(timeout=10)

    assert all(r["status"] == "completed" for r in (*r1, *r2))
    assert peak <= 2


async def test_child_isolates_parent_resumed_chat_run_claim(tmp_path: Path) -> None:
    from app.services.chat_run_state import chat_run_claim

    seen_child_claims: list[Any] = []

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        seen_child_claims.append(chat_run_claim.get())
        return _Outcome("isolated")

    parent_claim = ("chat_run_resumed_parent", "lease_tok_1")
    tok = chat_run_claim.set(parent_claim)
    try:
        [run] = await NativeSubagentExecutor(agent_factory=_FakeFactory(behaviour)).run_many(
            [SubagentSpec(goal="recon", role="scout")],
            _parent(tmp_path),
        )
        assert chat_run_claim.get() == parent_claim
    finally:
        chat_run_claim.reset(tok)

    assert run["status"] == "completed"
    assert run["parent_run_id"] == "chat_run_resumed_parent"
    assert run["child_run_id"].startswith("chat_run_resumed_parent:sub_")
    assert seen_child_claims == [None]


def test_plan_mode_tool_names_hides_delegate_task_when_backend_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.deep_think.plan_mode import plan_mode_tool_names

    offered = ["file_operations", "delegate_task", "plan_operation"]
    monkeypatch.setenv("DELEGATE_TASK_BACKEND", "cli")
    assert "delegate_task" not in plan_mode_tool_names(offered)

    monkeypatch.setenv("DELEGATE_TASK_BACKEND", "native")
    assert "delegate_task" in plan_mode_tool_names(offered)
