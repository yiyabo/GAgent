"""p75 native sub-agents: fail-closed inputs and real dispatch.

The end-to-end tests drive the real ``delegate_task`` registry entry, the real
``DeepThinkAgent`` tool loop and the real ``UnifiedToolExecutor`` with the real
``file_operations`` / ``document_reader`` handlers. Only the LLM is replaced:
a scripted, offline provider decides the tool calls from dummy text fixtures,
so no network, paid model, code, shell or bio tool is ever reached.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.llm import _usage_context as _llm_usage_ctx
from app.services.deep_think.plan_mode import plan_mode_denial, plan_mode_tool_allowed
from app.services.plans import native_subagent as ns
from app.services.plans.native_subagent import (
    NativeSubagentExecutor,
    ParentContext,
    SubagentInputError,
    SubagentSpec,
    enforce_workspace,
    resolve_inputs,
    validate_params,
)
from tool_box.context import ToolContext
from tool_box.tools_impl import delegate_task as dt


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    _llm_usage_ctx.set(None)
    monkeypatch.setenv("DELEGATE_TASK_ENABLED", "1")
    for name in (
        "DELEGATE_TASK_BACKEND",
        "DELEGATE_TASK_MAX_ITERATIONS",
        "DELEGATE_TASK_TIMEOUT_SECONDS",
        "DELEGATE_TASK_MAX_CONCURRENCY",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    _llm_usage_ctx.set(None)


class _Outcome:
    def __init__(self, answer: str, iterations: int = 1, termination: str = "final_answer") -> None:
        self.final_answer = answer
        self.total_iterations = iterations
        self.termination_reason = termination


class _agent_factory:
    """Fake agent factory that records every child it creates."""

    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour
        self.created: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        behaviour = self.behaviour

        class _Agent:
            async def think(self, prompt: str, context: dict[str, Any] | None = None) -> _Outcome:
                return await behaviour(prompt, kwargs)

        return _Agent()


# --- feature gate ------------------------------------------------------------------


async def test_feature_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import tool_schemas

    monkeypatch.delenv("DELEGATE_TASK_ENABLED", raising=False)
    assert dt.delegate_task_enabled() is False
    assert tool_schemas.delegate_task_enabled() is False
    result = await dt.delegate_task_handler(goal="summarise notes")
    assert result["success"] is False
    assert result["error"] == "delegate_task_disabled"


def test_deploy_template_keeps_the_feature_off() -> None:
    template = Path(__file__).resolve().parents[3] / "deploy" / ".env.template"
    lines = template.read_text(encoding="utf-8").splitlines()
    assert "DELEGATE_TASK_ENABLED=0" in lines
    assert "DELEGATE_TASK_ENABLED=1" not in lines


# --- child tool parameter validation -----------------------------------------------


@pytest.mark.parametrize(
    ("tool", "params", "detail"),
    [
        ("file_operations", {"operation": "read", "path": "a", "shell": "rm -rf /"}, "unknown parameter"),
        ("file_operations", {"path": "a"}, "'operation'"),
        ("file_operations", {"operation": 3, "path": "a"}, "'operation'"),
        ("file_operations", {"operation": "read"}, "'path' is required"),
        ("file_operations", {"operation": "read", "path": ["a"]}, "'path' must be"),
        ("file_operations", {"operation": "write", "path": "a"}, "'content' is required"),
        ("file_operations", {"operation": "write", "path": "a", "content": 5}, "'content' must be"),
        ("file_operations", {"operation": "list", "path": ".", "pattern": "../*"}, "'pattern'"),
        ("file_operations", {"operation": "list", "path": ".", "pattern": "/etc/*"}, "'pattern'"),
        ("document_reader", {"operation": "read_text"}, "'file_path' is required"),
        ("document_reader", {"operation": "exec", "file_path": "a.txt"}, "not allowed"),
        ("document_reader", {"operation": "read_text", "file_path": "notes/*.txt"}, "wildcard"),
        ("vision_reader", {"operation": "describe_figure"}, "'file_path' is required"),
        ("vision_reader", {"operation": "shell", "file_path": "a.png"}, "not allowed"),
        ("web_search", {"query": "x"}, "not available"),
    ],
)
def test_malformed_child_tool_calls_fail_closed(tool: str, params: dict[str, Any], detail: str) -> None:
    refusal, _ = validate_params(tool, params)
    assert refusal is not None
    assert refusal["success"] is False and refusal["executed"] is False
    assert refusal["error"] in {"subagent_invalid_params", "subagent_tool_not_allowed"}
    assert detail in refusal["summary"]


def test_valid_child_tool_call_drops_only_injected_context() -> None:
    params = {"operation": "read_text", "file_path": "a.txt", "tool_context": object()}
    refusal, cleaned = validate_params("document_reader", params)
    assert refusal is None
    assert cleaned == {"operation": "read_text", "file_path": "a.txt"}


def test_workspace_boundary_for_reads_and_writes(tmp_path: Path) -> None:
    scratch = tmp_path / "subagents" / "abc"
    scratch.mkdir(parents=True)
    inputs = tmp_path / "notes"
    inputs.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    link = inputs / "escape.txt"
    link.symlink_to(tmp_path / "secret.txt")

    def check(tool: str, params: dict[str, Any]) -> dict[str, Any] | None:
        refusal, _ = enforce_workspace(tool, params, write_root=scratch, read_roots=[inputs])
        return refusal

    assert check("file_operations", {"operation": "read", "path": str(inputs / "a.txt")}) is None
    assert check("file_operations", {"operation": "write", "path": "out.md", "content": "x"}) is None
    denied = [
        ("file_operations", {"operation": "read", "path": str(tmp_path / "secret.txt")}),
        ("file_operations", {"operation": "read", "path": "../../secret.txt"}),
        ("file_operations", {"operation": "read", "path": str(link)}),
        ("file_operations", {"operation": "write", "path": str(inputs / "a.txt"), "content": "x"}),
        ("document_reader", {"operation": "read_text", "file_path": str(tmp_path / "secret.txt")}),
    ]
    for tool, params in denied:
        refusal = check(tool, params)
        assert refusal is not None and refusal["error"] == "subagent_path_outside_workspace", params


def test_resolve_inputs_is_strict(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "subagents" / "old").mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}_outside.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        assert resolve_inputs(["notes", "notes/a.txt", "notes"], tmp_path) == [
            (tmp_path / "notes").resolve(),
            (tmp_path / "notes" / "a.txt").resolve(),
        ]
        for bad in ("missing.txt", "../" + outside.name, str(outside), "subagents/old", ".", ""):
            with pytest.raises(SubagentInputError):
                resolve_inputs([bad], tmp_path)
    finally:
        outside.unlink()


# --- handler validation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"goal": 5}, "invalid_goal"),
        ({"goal": "g", "role": 3}, "invalid_params"),
        ({"goal": "g", "deliverable": ["x"]}, "invalid_params"),
        ({"goal": "g", "context_paths": "notes"}, "invalid_params"),
        ({"goal": "g", "context_paths": ["notes", 7]}, "invalid_params"),
        ({"goal": "g", "context_paths": [f"p{i}" for i in range(21)]}, "invalid_params"),
        ({"goal": "x" * 8001}, "invalid_params"),
        ({"tasks": [{"goal": "a", "tools": ["execute_code"]}]}, "invalid_tasks"),
        ({"tasks": [{"goal": "a", "context_paths": "notes"}]}, "invalid_tasks"),
        ({"tasks": "a"}, "invalid_tasks"),
        ({"tasks": ["a"]}, "invalid_tasks"),
        ({"tasks": [{"goal": "a", "role": "admin"}]}, "invalid_role"),
    ],
)
async def test_handler_fails_closed_before_any_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], error: str
) -> None:
    factory = _agent_factory(lambda *_: None)
    monkeypatch.setattr(dt, "_new_native_executor", lambda: NativeSubagentExecutor(agent_factory=factory))
    result = await dt.delegate_task_handler(**kwargs, tool_context=ToolContext(work_dir=str(tmp_path)))
    assert result["success"] is False
    assert result["error"] == error
    assert factory.created == []
    assert not (tmp_path / "subagents").exists()


@pytest.mark.parametrize("paths", [["missing.txt"], ["../outside"], ["/etc"], ["."], ["subagents"]])
async def test_invalid_context_paths_refuse_the_whole_fan_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paths: list[str]
) -> None:
    (tmp_path / "notes.txt").write_text("n", encoding="utf-8")
    (tmp_path / "subagents").mkdir()
    factory = _agent_factory(lambda *_: None)
    monkeypatch.setattr(dt, "_new_native_executor", lambda: NativeSubagentExecutor(agent_factory=factory))
    result = await dt.delegate_task_handler(
        tasks=[{"goal": "ok", "context_paths": ["notes.txt"]}, {"goal": "bad", "context_paths": paths}],
        tool_context=ToolContext(work_dir=str(tmp_path)),
    )
    assert result["success"] is False
    assert result["error"] == "invalid_context_paths"
    assert factory.created == []
    assert list((tmp_path / "subagents").iterdir()) == []


async def test_relative_work_dir_without_session_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _agent_factory(lambda *_: None)
    monkeypatch.setattr(dt, "_new_native_executor", lambda: NativeSubagentExecutor(agent_factory=factory))
    result = await dt.delegate_task_handler(goal="g", tool_context=ToolContext(work_dir="relative/dir"))
    assert result["error"] == "invalid_context_paths"
    assert factory.created == []


# --- CLI backend role safety -------------------------------------------------------


async def test_cli_backend_refuses_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DELEGATE_TASK_BACKEND", "cli")

    def _boom() -> Any:
        raise AssertionError("the CLI executor must not start")

    monkeypatch.setattr(dt, "_new_executor", _boom)
    result = await dt.delegate_task_handler(goal="look around", role="scout")
    assert result["error"] == "role_unsupported_backend"


def test_plan_mode_refuses_scout_delegation_on_the_cli_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    params = {"goal": "g", "role": "scout"}
    assert plan_mode_tool_allowed("delegate_task", params) is True
    monkeypatch.setenv("DELEGATE_TASK_BACKEND", "cli")
    assert plan_mode_tool_allowed("delegate_task", params) is False
    denial = plan_mode_denial("delegate_task", params)
    assert denial["error"] == "plan_mode_read_only"
    assert "CLI delegation backend" in denial["summary"]


# --- concurrency -------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [("", 4), ("2", 2), ("6", 6), ("0", 4), ("7", 4), ("x", 4)])
def test_configured_max_concurrency_is_bounded(monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("DELEGATE_TASK_MAX_CONCURRENCY", raw)
    assert ns.configured_max_concurrency() == expected


@pytest.mark.parametrize("raw", ["nan", "inf", "-1", "0", "soon"])
def test_invalid_timeout_env_falls_back(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("DELEGATE_TASK_TIMEOUT_SECONDS", raw)
    assert ns._timeout_seconds() == ns.DEFAULT_TIMEOUT_SECONDS


async def test_process_wide_limit_spans_concurrent_delegate_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DELEGATE_TASK_MAX_CONCURRENCY", "2")
    monkeypatch.setattr(ns, "_PROCESS_LIMITERS", {})
    active = 0
    peak = 0

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return _Outcome("ok")

    def executor() -> NativeSubagentExecutor:
        return NativeSubagentExecutor(agent_factory=_agent_factory(behaviour), max_concurrency=4)

    parent = ParentContext(work_dir=str(tmp_path))
    batches = await asyncio.gather(
        executor().run_many([SubagentSpec(goal=f"a{i}") for i in range(3)], parent),
        executor().run_many([SubagentSpec(goal=f"b{i}") for i in range(3)], parent),
    )
    assert [run["status"] for batch in batches for run in batch] == ["completed"] * 6
    assert peak == 2


async def test_outer_cancellation_stops_every_child(tmp_path: Path) -> None:
    started = asyncio.Event()
    stopped: list[str] = []

    async def behaviour(prompt: str, kwargs: dict[str, Any]) -> _Outcome:
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            stopped.append(prompt.splitlines()[3])
        return _Outcome("never")

    executor = NativeSubagentExecutor(agent_factory=_agent_factory(behaviour))
    task = asyncio.ensure_future(
        executor.run_many([SubagentSpec(goal="one"), SubagentSpec(goal="two")], ParentContext(work_dir=str(tmp_path)))
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(stopped) == ["one", "two"]
    assert list((tmp_path / "subagents").iterdir()) == []


async def test_parent_cancel_before_start_spawns_nothing(tmp_path: Path) -> None:
    cancel = asyncio.Event()
    cancel.set()
    factory = _agent_factory(lambda *_: None)
    runs = await NativeSubagentExecutor(agent_factory=factory).run_many(
        [SubagentSpec(goal="a"), SubagentSpec(goal="b")], ParentContext(work_dir=str(tmp_path), parent_cancel=cancel)
    )
    assert [run["status"] for run in runs] == ["cancelled", "cancelled"]
    assert factory.created == []
    assert not (tmp_path / "subagents").exists()


# --- result sanitizer --------------------------------------------------------------


def test_sanitizer_keeps_a_compact_subagent_runs_projection() -> None:
    from app.routers.chat.tool_results import sanitize_tool_result

    run = {
        "subrun_id": "abc",
        "child_run_id": "run_1:sub_abc",
        "parent_run_id": "run_1",
        "role": "worker",
        "goal": "g",
        "status": "completed",
        "iterations": 3,
        "summary": "wrote summary.md",
        "artifact_paths": ["/s/subagents/abc/summary.md"],
        "workspace": "/s/subagents/abc",
        "duration_ms": 12.5,
        "error": None,
        "transcript": "must not leak",
    }
    sanitized = sanitize_tool_result(
        "delegate_task", {"tool": "delegate_task", "success": True, "status": "completed", "subagent_runs": [run]}
    )
    assert sanitized["subagent_runs"] == [
        {
            "subrun_id": "abc",
            "child_run_id": "run_1:sub_abc",
            "role": "worker",
            "status": "completed",
            "workspace": "/s/subagents/abc",
            "iterations": 3,
            "duration_ms": 12.5,
            "summary": "wrote summary.md",
            "artifact_paths": ["/s/subagents/abc/summary.md"],
        }
    ]


# --- real dispatch: registry -> DeepThinkAgent -> UnifiedToolExecutor -> handlers ----


class _ScriptedProvider:
    """Offline stand-in for the LLM: each child's goal selects a fixed tool script."""

    def __init__(self, scripts: dict[str, list[tuple[str, dict[str, Any]]]], answers: dict[str, str]) -> None:
        self.scripts = scripts
        self.answers = answers
        self.observations: dict[str, list[str]] = {key: [] for key in scripts}
        self.calls = 0

    def _key(self, messages: list[dict[str, Any]]) -> str:
        text = json.dumps(messages, ensure_ascii=False, default=str)
        return next(key for key in self.scripts if key in text)

    async def stream_chat_with_tools_async(self, **kwargs: Any) -> NativeStreamResult:
        self.calls += 1
        messages = kwargs["messages"]
        key = self._key(messages)
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            self.observations[key].append(str(last.get("content")))
        script = self.scripts[key]
        step = len(self.observations[key])
        if step < len(script):
            name, params = script[step]
            call = NativeToolCall(f"{key}_{step}", name, params)
        else:
            call = NativeToolCall(
                f"{key}_final", "submit_final_answer", {"answer": self.answers[key], "confidence": 0.9}
            )
        return NativeStreamResult(content="", tool_calls=[call], finish_reason="tool_calls")

    async def stream_chat_async(self, **kwargs: Any):  # pragma: no cover - native loop only
        raise AssertionError("the native tool loop must be used")
        yield ""

    async def chat_async(self, **kwargs: Any) -> str:  # pragma: no cover - no summarisation step
        return ""


@pytest.fixture
def session_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.services import path_router
    from app.services.session_paths import get_runtime_session_dir
    from tool_box.tools_impl import file_operations

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(path_router, "_default_router", None)
    monkeypatch.setattr(file_operations, "_get_project_root", lambda: tmp_path.resolve())
    session_id = "session_p75_e2e"
    session_dir = get_runtime_session_dir(session_id, create=True).resolve()
    notes = session_dir / "notes"
    notes.mkdir()
    (notes / "meeting_a.txt").write_text("Decision: adopt plan A.\nOpen: budget owner?\n", encoding="utf-8")
    (notes / "meeting_b.txt").write_text("Decision: ship on Friday.\n", encoding="utf-8")
    (session_dir / "private.txt").write_text("DUMMY-SECRET-TOKEN", encoding="utf-8")
    return session_id, session_dir


def _use_real_agent(monkeypatch: pytest.MonkeyPatch, provider: _ScriptedProvider, seen: list[tuple[str, dict]]):
    from app.services.deep_think_agent import DeepThinkAgent

    real_executor = ns._default_tool_executor

    async def recording_executor(tool_name: str, params: dict[str, Any], context: Any) -> dict[str, Any]:
        seen.append((tool_name, dict(params)))
        return await real_executor(tool_name, params, context)

    monkeypatch.setattr(ns, "_default_agent_factory", lambda **kw: DeepThinkAgent(llm_client=provider, **kw))
    monkeypatch.setattr(ns, "_default_tool_executor", recording_executor)


async def test_real_parallel_dispatch_places_files_scopes_reads_and_cleans_up(
    session_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tool_box import execute_tool
    from tool_box.tool_registry import register_all_tools

    session_id, session_dir = session_runtime
    notes = session_dir / "notes"
    provider = _ScriptedProvider(
        scripts={
            "SCOUT-NOTES": [
                ("file_operations", {"operation": "list", "path": str(notes)}),
                ("file_operations", {"operation": "read", "path": str(notes / "meeting_a.txt")}),
                ("file_operations", {"operation": "read", "path": str(session_dir / "private.txt")}),
                ("file_operations", {"operation": "write", "path": "scout.md", "content": "x"}),
                ("execute_code", {"code": "print(open('/etc/passwd').read())"}),
            ],
            "WORKER-SUMMARY": [
                ("document_reader", {"operation": "read_text", "file_path": str(notes / "meeting_b.txt")}),
                (
                    "file_operations",
                    {"operation": "write", "path": "summary.md", "content": "# Decisions\n- ship on Friday\n"},
                ),
                ("file_operations", {"operation": "write", "path": str(notes / "meeting_b.txt"), "content": "x"}),
                ("document_reader", {"operation": "read_text", "file_path": str(session_dir / "private.txt")}),
            ],
            "VERIFIER-CHECK": [
                ("file_operations", {"operation": "info", "path": str(notes / "meeting_a.txt")}),
                ("vision_reader", {"operation": "run_shell", "file_path": str(notes / "meeting_a.txt")}),
            ],
        },
        answers={
            "SCOUT-NOTES": "Two meeting notes; A decides plan A.",
            "WORKER-SUMMARY": "Wrote summary.md",
            "VERIFIER-CHECK": "meeting_a.txt exists",
        },
    )
    seen: list[tuple[str, dict[str, Any]]] = []
    _use_real_agent(monkeypatch, provider, seen)
    progress: list[dict[str, Any]] = []

    async def on_progress(event: dict[str, Any]) -> None:
        progress.append(event)

    register_all_tools()
    result = await execute_tool(
        "delegate_task",
        tasks=[
            {"goal": "SCOUT-NOTES: list the notes", "role": "scout", "context_paths": ["notes"]},
            {
                "goal": "WORKER-SUMMARY: summarise meeting b",
                "role": "worker",
                "deliverable": "summary.md",
                "context_paths": ["notes/meeting_b.txt"],
            },
            {"goal": "VERIFIER-CHECK: confirm meeting a", "role": "verifier", "context_paths": ["notes"]},
        ],
        tool_context=ToolContext(session_id=session_id, job_id="chat_run_e2e", on_progress=on_progress),
    )

    assert result["success"] is True, result
    assert result["status"] == "completed"
    scout, worker, verifier = result["subagent_runs"]
    assert [r["status"] for r in (scout, worker, verifier)] == ["completed"] * 3
    assert all(r["parent_run_id"] == "chat_run_e2e" for r in (scout, worker, verifier))
    assert all(r["child_run_id"] == f"chat_run_e2e:sub_{r['subrun_id']}" for r in (scout, worker, verifier))

    # File placement: only the worker produced a file, inside its own scratch dir.
    summary = session_dir / "subagents" / worker["subrun_id"] / "summary.md"
    assert summary.read_text(encoding="utf-8") == "# Decisions\n- ship on Friday\n"
    assert worker["artifact_paths"] == [str(summary)]
    assert result["artifact_paths"] == [str(summary)]
    assert worker["workspace"] == str(summary.parent)
    # Cleanup: read-only children leave no empty scratch dirs behind.
    assert scout["workspace"] is None and verifier["workspace"] is None
    assert sorted(p.name for p in (session_dir / "subagents").iterdir()) == [worker["subrun_id"]]
    # The selected input is untouched.
    assert (notes / "meeting_b.txt").read_text(encoding="utf-8") == "Decision: ship on Friday.\n"

    # Scoped reads: allowed reads reached the real handlers and returned the fixtures ...
    scout_obs = provider.observations["SCOUT-NOTES"]
    worker_obs = provider.observations["WORKER-SUMMARY"]
    verifier_obs = provider.observations["VERIFIER-CHECK"]
    assert "meeting_a.txt" in scout_obs[0] and "meeting_b.txt" in scout_obs[0]
    assert "adopt plan A" in scout_obs[1]
    assert "ship on Friday" in worker_obs[0]
    # ... everything else was refused before any handler ran.
    assert "subagent_path_outside_workspace" in scout_obs[2]
    assert "subagent_read_only" in scout_obs[3]
    assert "execute_code" in scout_obs[4] and "DUMMY-SECRET-TOKEN" not in scout_obs[4]
    assert "subagent_path_outside_workspace" in worker_obs[2]
    assert "subagent_path_outside_workspace" in worker_obs[3]
    assert "subagent_invalid_params" in verifier_obs[1]
    assert not any("DUMMY-SECRET-TOKEN" in text for obs in provider.observations.values() for text in obs)
    assert sorted({name for name, _ in seen}) == ["document_reader", "file_operations"]
    assert all("private.txt" not in json.dumps(params) for _, params in seen)
    scripted = [(name, params) for name, params in seen if params.get("operation") != "profile"]
    assert len(scripted) == 5  # list + read (scout), read + write (worker), info (verifier)
    # The agent's own directory-profile gate runs at most once per child, inside its roots.
    forced = [params["path"] for name, params in seen if params.get("operation") == "profile"]
    assert len(forced) <= 3
    assert provider.calls <= 20, provider.calls  # no gate loop burning the iteration budget
    assert all(Path(path).is_relative_to(session_dir / "subagents") for path in forced), forced

    # Parent attribution in the activity stream.
    stages = sorted((e["role"], e["stage"]) for e in progress)
    assert stages == sorted(
        [(role, stage) for role in ("scout", "worker", "verifier") for stage in ("started", "completed")]
    )
    assert all(e["parent_run_id"] == "chat_run_e2e" and e["message"] for e in progress)


async def test_real_dispatch_parent_cancel_stops_a_running_child(
    session_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tool_box import execute_tool
    from tool_box.tool_registry import register_all_tools

    session_id, session_dir = session_runtime
    monkeypatch.setattr(ns, "CANCEL_GRACE_SECONDS", 0.2)
    abort = asyncio.Event()

    class _HangingProvider(_ScriptedProvider):
        async def stream_chat_with_tools_async(self, **kwargs: Any) -> NativeStreamResult:
            abort.set()
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    provider = _HangingProvider(scripts={"HANG": []}, answers={"HANG": ""})
    _use_real_agent(monkeypatch, provider, [])
    register_all_tools()
    started = time.perf_counter()
    result = await execute_tool(
        "delegate_task",
        goal="HANG: wait forever",
        role="scout",
        context_paths=["notes"],
        tool_context=ToolContext(session_id=session_id, job_id="chat_run_cancel", abort_event=abort),
    )
    assert time.perf_counter() - started < 5
    assert result["status"] == "cancelled", result
    assert result["success"] is False
    [run] = result["subagent_runs"]
    assert run["status"] == "cancelled" and run["error"] == "cancelled"
    assert run["workspace"] is None
    assert list((session_dir / "subagents").iterdir()) == []
