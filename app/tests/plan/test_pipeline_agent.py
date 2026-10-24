"""The agent loop with a scripted model: tool calls edit and save the draft, answers stream, turns can be undone."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.database import init_db
from app.llm import NativeStreamResult, NativeToolCall
from app.services.pipelines import agent, service


class ScriptedModel:
    """Each call to the model returns the next scripted step: (text, [(tool, arguments), ...])."""

    def __init__(self, steps: List[Any]) -> None:
        self.steps = list(steps)
        self.requests: List[Dict[str, Any]] = []
        self.release: asyncio.Event | None = None

    async def stream_chat_with_tools_async(self, messages, tools, on_content_delta=None, **_):
        self.requests.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
        if self.release is not None:
            await self.release.wait()
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        text, calls = step
        if text and on_content_delta:
            await on_content_delta(text)
        return NativeStreamResult(
            content=text,
            tool_calls=[NativeToolCall(id=f"call_{len(self.requests)}_{index}", name=name, arguments=args) for index, (name, args) in enumerate(calls)],
            finish_reason="tool_calls" if calls else "stop",
        )


@pytest.fixture
def env(isolated_app_env, monkeypatch):
    init_db()
    started: List[str] = []
    monkeypatch.setattr(service, "launch", lambda run_id: started.append(run_id))
    holder = {"model": None}
    monkeypatch.setattr(agent, "_make_client", lambda: holder["model"])
    return holder, started


def new_pipeline() -> str:
    return service.create("owner-1", "Agent test", "", None)["id"]


def use(env, *steps) -> ScriptedModel:
    env[0]["model"] = ScriptedModel(list(steps))
    return env[0]["model"]


async def talk(pipeline_id: str, text: str, selected=None) -> List[Dict[str, Any]]:
    events = []
    async for event in agent.start_turn(pipeline_id, "owner-1", text, selected):
        events.append(event)
    return events


BUILD = [
    ("add_node", {"type": "ncbi.genome", "id": "lambda", "label": "lambda", "params": {"accessions": "NC_001416.1"}}),
    ("add_node", {"type": "prodigal.predict", "id": "genes", "inputs": {"genome": "lambda.genome"}}),
    ("add_node", {"type": "seqkit.stats", "id": "stats", "inputs": {"sequences": "lambda.genome"}}),
]


async def test_the_agent_builds_the_pipeline_streams_text_and_saves_after_each_step(env):
    model = use(env, ("I'll build it.", BUILD), ("Done: genes and stats for lambda.", [("validate", {})]), ("Ready.", []))
    pipeline_id = new_pipeline()
    events = await talk(pipeline_id, "Predict genes for lambda and give me stats", selected=None)

    kinds = [event["type"] for event in events]
    assert kinds.count("tool") == 4 and kinds.count("graph") == 1 and kinds[-1] == "done"
    draft = service.load(pipeline_id)["draft"]
    assert set(draft["nodes"]) == {"lambda", "genes", "stats"}
    assert draft["nodes"]["genes"]["inputs"]["genome"] == {"node": "lambda", "port": "genome"}
    graph_event = next(event for event in events if event["type"] == "graph")
    assert set(graph_event["graph"]["nodes"]) == {"lambda", "genes", "stats"} and graph_event["validation"]["ok"]
    done = events[-1]["message"]
    assert "Ready." in done["content"] and done["meta"]["can_undo"] is True
    assert any(event["type"] == "delta" and event["text"] == "I'll build it." for event in events)

    first = model.requests[0]["messages"]
    assert first[0]["role"] == "system" and "NODE CATALOG" in first[0]["content"] and "The canvas is empty." in first[0]["content"]
    assert first[-1] == {"role": "user", "content": "Predict genes for lambda and give me stats"}
    tool_results = [m for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_results) == 3 and all(json.loads(m["content"])["ok"] for m in tool_results)


async def test_mistakes_come_back_as_results_the_model_can_fix(env):
    model = use(
        env,
        ("", [("add_node", {"type": "blast.blastp", "id": "bp"}), ("connect", {"from": "bp.hits", "to": "bp.query"})]),
        ("", [("set_params", {"node": "bp", "params": {"evalue": "loud"}})]),
        ("Sorry, that did not fit.", []),
    )
    pipeline_id = new_pipeline()
    events = await talk(pipeline_id, "blast something")
    tools = [event for event in events if event["type"] == "tool"]
    assert [item["ok"] for item in tools] == [True, False, False]
    assert "type mismatch" in tools[1]["error"] or "own output" in tools[1]["error"]
    second = [m for m in model.requests[1]["messages"] if m["role"] == "tool"]
    assert json.loads(second[1]["content"])["ok"] is False
    assert "Sorry" in events[-1]["message"]["content"]


async def test_history_selection_and_state_reach_the_next_turn(env):
    model = use(env, ("", BUILD[:1]), ("Added the genome.", []), ("It is the lambda download.", []))
    pipeline_id = new_pipeline()
    await talk(pipeline_id, "add lambda")
    await talk(pipeline_id, "what is this node?", selected="lambda")
    request = model.requests[-1]["messages"]
    assert "selected node lambda" in request[0]["content"] and "lambda [ncbi.genome]" in request[0]["content"]
    roles = [m["role"] for m in request[1:]]
    assert roles == ["user", "assistant", "user"]
    assert "Changes made on the canvas" in request[2]["content"] and "added lambda" in request[2]["content"]

    saved = agent.history(pipeline_id)
    assert [m["role"] for m in saved] == ["user", "assistant", "user", "assistant"]
    assert saved[1]["meta"]["can_undo"] is True and "before_graph" not in saved[1]["meta"]
    assert saved[2]["meta"] == {"selected_node": "lambda", "can_undo": False}


async def test_a_turn_can_be_undone_once_and_only_the_latest(env):
    use(env, ("", BUILD[:1]), ("one", []), ("", BUILD[1:2]), ("two", []))
    pipeline_id = new_pipeline()
    await talk(pipeline_id, "first")
    await talk(pipeline_id, "second")
    messages = [m for m in agent.history(pipeline_id) if m["role"] == "assistant"]
    assert set(service.load(pipeline_id)["draft"]["nodes"]) == {"lambda", "genes"}
    with pytest.raises(service.PipelineError) as old:
        agent.undo(pipeline_id, messages[0]["id"])
    assert old.value.status == 409
    restored = agent.undo(pipeline_id, messages[1]["id"])
    assert set(restored["draft"]["nodes"]) == {"lambda"}
    assert set(service.load(pipeline_id)["draft"]["nodes"]) == {"lambda"}
    with pytest.raises(service.PipelineError) as again:
        agent.undo(pipeline_id, messages[1]["id"])
    assert again.value.status == 404
    agent.undo(pipeline_id, messages[0]["id"])
    assert service.load(pipeline_id)["draft"]["nodes"] == {}


async def test_the_agent_cannot_run_the_pipeline_only_ask(env):
    uploads = service.session_dir(pid := new_pipeline()) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "aa11bb22_g.fasta").write_text(">a\nACGT\n")
    use(
        env,
        ("", [("add_node", {"type": "input.genome", "id": "g", "params": {"path": "g.fasta"}}), ("add_node", {"type": "seqkit.stats", "id": "s", "inputs": {"sequences": "g.file"}})]),
        ("", [("request_run", {"reason": "Count the sequences (a few seconds)."})]),
        ("Please confirm the run.", []),
    )
    events = await talk(pid, "build and run stats on my file")
    request = next(event for event in events if event["type"] == "run_request")
    assert request["state"] == "pending" and "Count" in request["reason"]
    assert not service.list_runs(pid), "nothing runs without the user's answer"

    message = events[-1]["message"]
    assert message["meta"]["run_request"]["state"] == "pending"
    answer = agent.resolve_run_request(pid, "owner-1", message["id"], "run")
    assert answer["state"] == "approved" and answer["run"]["status"] in {"queued", "running"}
    assert len(service.list_runs(pid)) == 1
    with pytest.raises(service.PipelineError) as twice:
        agent.resolve_run_request(pid, "owner-1", message["id"], "run")
    assert twice.value.status == 409


async def test_a_run_request_for_an_unready_pipeline_is_refused(env):
    use(env, ("", [("add_node", {"type": "input.genome", "id": "g"}), ("request_run", {"reason": "go"})]), ("Pick a file first.", []))
    pid = new_pipeline()
    events = await talk(pid, "run it")
    assert not any(event["type"] == "run_request" for event in events)
    assert [item["ok"] for item in events if item["type"] == "tool"] == [True, False]


async def test_read_output_returns_what_the_latest_run_wrote(env, tmp_path):
    use(env, ("", [("read_output", {"node": "stats", "port": "stats"})]), ("The file has one sequence.", []))
    pid = new_pipeline()
    out = Path(service.session_dir(pid)) / "pipeline_runs" / "r1" / "stats.txt"
    out.parent.mkdir(parents=True)
    out.write_text("file\tnum_seqs\nx\t1\n")
    graph = {"schema": 1, "nodes": {"g": {"type": "input.genome", "params": {}}, "stats": {"type": "seqkit.stats", "inputs": {"sequences": {"node": "g", "port": "file"}}}}}
    service.update(pid, graph=graph)
    from app.database_pool import get_db
    from app.repository import pipelines as repo

    with get_db() as conn:
        version = repo.freeze_version(conn, pid, graph, "h")
        run_id = repo.create_run(conn, pipeline_id=pid, version=version, owner_id="owner-1", session_id=f"pipeline_{pid}", inputs={})
        repo.init_node_runs(conn, run_id, ["stats"])
        repo.finish_node(conn, run_id, "stats", "succeeded", outputs={"stats": {"path": str(out), "type": "table", "kind": "file"}})
    events = await talk(pid, "what did stats say?")
    assert [item["ok"] for item in events if item["type"] == "tool"] == [True]
    result = json.loads([m for m in env[0]["model"].requests[1]["messages"] if m["role"] == "tool"][0]["content"])
    assert result["ok"] and "num_seqs" in result["text"] and result["empty"] is False


async def test_a_model_failure_is_reported_and_saved(env):
    use(env, RuntimeError("provider down"))
    pid = new_pipeline()
    events = await talk(pid, "hello")
    assert events[-2]["type"] == "error" and "provider down" in events[-2]["message"]
    assert "could not finish" in events[-1]["message"]["content"]
    assert not agent.busy(pid)


async def test_only_one_turn_runs_at_a_time_and_empty_messages_are_refused(env):
    model = use(env, ("answer", []))
    model.release = asyncio.Event()
    pid = new_pipeline()
    stream = agent.start_turn(pid, "owner-1", "first")
    await asyncio.sleep(0.05)
    assert agent.busy(pid)
    with pytest.raises(service.PipelineError) as second:
        agent.start_turn(pid, "owner-1", "second")
    assert second.value.status == 409
    with pytest.raises(service.PipelineError) as clearing:
        agent.clear(pid)
    assert clearing.value.status == 409
    model.release.set()
    events = [event async for event in stream]
    assert events[-1]["type"] == "done"
    with pytest.raises(service.PipelineError) as empty:
        agent.start_turn(pid, "owner-1", "   ")
    assert empty.value.status == 422
    agent.clear(pid)
    assert agent.history(pid) == []


async def test_leaving_the_stream_early_does_not_lose_the_turn(env):
    use(env, ("", BUILD[:1]), ("saved anyway", []))
    pid = new_pipeline()
    stream = agent.start_turn(pid, "owner-1", "add lambda")
    first = await stream.__anext__()
    assert first["type"] in {"tool", "delta"}
    await stream.aclose()
    for _ in range(100):
        if not agent.busy(pid):
            break
        await asyncio.sleep(0.02)
    assert set(service.load(pid)["draft"]["nodes"]) == {"lambda"}
    assert [m["role"] for m in agent.history(pid)] == ["user", "assistant"]
