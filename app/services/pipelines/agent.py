"""The pipeline agent: a conversation beside the canvas that can also edit the canvas.

One turn is: the user's message, then the model's tool calls (each applied to the draft and saved at once)
until it answers in words. A turn runs as its own task and streams events to whoever is listening; if the
browser goes away the turn still finishes and is saved, and the conversation is there when the user comes
back. Only one turn runs per pipeline at a time. Before a turn changes anything the previous draft is kept
with the answer, so the user can undo the whole turn. The agent cannot start runs: ``request_run`` leaves a
confirmation for the user, and only the user's approval starts one.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from app.database_pool import get_db
from app.repository import pipelines as repo

from . import agent_context, service
from .agent_tools import GraphEditor, tool_schemas

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 24
MAX_MESSAGE_CHARS = 8000
HISTORY_MESSAGES = 24
MAX_ANSWER_TOKENS = 4096
MODEL_TIMEOUT_SECONDS = 180
READ_LIMIT_CHARS = 6000

_running: Dict[str, asyncio.Task] = {}
_background: set = set()


def busy(pipeline_id: str) -> bool:
    task = _running.get(pipeline_id)
    return bool(task and not task.done())


# ------------------------------------------------------------------ history
def history(pipeline_id: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        messages = repo.list_messages(conn, pipeline_id)
    for message in messages:
        # The saved copy of the draft before a turn is for undo; the browser only needs to know it exists.
        meta = dict(message.get("meta") or {})
        meta["can_undo"] = bool(meta.pop("before_graph", None)) and not meta.get("undone")
        message["meta"] = meta
    return messages


def clear(pipeline_id: str) -> None:
    if busy(pipeline_id):
        raise service.PipelineError(409, "the agent is working; wait for it to finish")
    with get_db() as conn:
        repo.clear_messages(conn, pipeline_id)


def _history_for_model(pipeline_id: str) -> List[Dict[str, str]]:
    with get_db() as conn:
        messages = repo.list_messages(conn, pipeline_id, HISTORY_MESSAGES)
    result: List[Dict[str, str]] = []
    for message in messages:
        text = message["content"]
        actions = (message.get("meta") or {}).get("actions") or []
        if message["role"] == "assistant" and actions:
            done = "; ".join(item["summary"] for item in actions if item.get("ok"))
            if done:
                text += f"\n[Changes made on the canvas in that turn: {done}]"
        result.append({"role": message["role"], "content": text})
    return result


# ------------------------------------------------------------------ one turn
def _latest_run(pipeline_id: str) -> Optional[Dict[str, Any]]:
    runs = service.list_runs(pipeline_id, 1)
    return service.run_detail(runs[0]["id"]) if runs else None


def _file_paths(pipeline_id: str) -> List[str]:
    return [item["path"] for item in service.list_files(pipeline_id)]


def _summary(name: str, args: Dict[str, Any], result: Dict[str, Any]) -> str:
    if name == "add_node":
        return f"added {result.get('node') or args.get('type')} ({args.get('type')})"
    if name == "remove_node":
        return f"removed {args.get('node')}"
    if name == "connect":
        return f"linked {args.get('from')} -> {args.get('to')}"
    if name == "disconnect":
        return f"unlinked {args.get('to')}"
    if name == "set_params":
        return f"set {args.get('node')}: " + ", ".join(f"{key}={value}" for key, value in (result.get("set") or {}).items())
    if name == "set_label":
        return f"labelled {args.get('node')} \"{args.get('label')}\""
    if name == "tidy_layout":
        return "tidied the layout"
    if name == "validate":
        return "checked the pipeline: " + ("ready to run" if result.get("ready_to_run") else f"{len(result.get('errors') or [])} problem(s)")
    if name == "read_output":
        return f"read {args.get('node')}.{args.get('port')}"
    if name == "request_run":
        return "asked to run the pipeline"
    return name


def _read_output(pipeline_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    runs = service.list_runs(pipeline_id, 1)
    if not runs:
        return {"ok": False, "error": "there is no run yet, so there are no results to read"}
    try:
        preview = service.preview(runs[0]["id"], str(args.get("node") or ""), str(args.get("port") or ""), args.get("path") or None)
    except service.PipelineError as exc:
        return {"ok": False, "error": str(exc)}
    if preview["kind"] == "text":
        text = preview["text"]
        return {
            "ok": True,
            "name": preview["name"],
            "size": preview["size"],
            "text": text[:READ_LIMIT_CHARS],
            "truncated": preview["truncated"] or len(text) > READ_LIMIT_CHARS,
            "empty": preview["size"] == 0,
        }
    if preview["kind"] in {"dir", "prefix"}:
        entries = preview.get("entries") or []
        return {"ok": True, "folder": True, "files": entries[:80], "note": "read one with the path argument"}
    return {"ok": True, "binary": True, "name": preview.get("name"), "size": preview.get("size")}


def _apply(editor: GraphEditor, pipeline_id: str, name: str, args: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    if name == "add_node":
        return editor.add_node(args.get("type"), args.get("id"), args.get("label"), args.get("params"), args.get("inputs"))
    if name == "remove_node":
        return editor.remove_node(args.get("node"))
    if name == "connect":
        return editor.connect(args.get("from"), args.get("to"))
    if name == "disconnect":
        return editor.disconnect(args.get("to"), args.get("from_node"))
    if name == "set_params":
        return editor.set_params(args.get("node"), args.get("params"))
    if name == "set_label":
        return editor.set_label(args.get("node"), args.get("label"))
    if name == "tidy_layout":
        return editor.tidy()
    if name == "validate":
        return editor.validate()
    if name == "read_output":
        return _read_output(pipeline_id, args)
    if name == "request_run":
        check = editor.validate()
        if not check["ready_to_run"]:
            return {"ok": False, "error": "the pipeline is not ready to run yet", "errors": check["errors"]}
        state["run_request"] = {"reason": str(args.get("reason") or "")[:400], "state": "pending"}
        return {"ok": True, "note": "the user was asked to confirm; the pipeline has not been started"}
    return {"ok": False, "error": f"unknown tool {name!r}"}


def _call_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


async def _model_step(client: Any, messages: List[Dict[str, Any]], emit: Any) -> Any:
    async def on_delta(text: str) -> None:
        if text:
            await emit({"type": "delta", "text": text})

    return await asyncio.wait_for(
        client.stream_chat_with_tools_async(
            messages=messages,
            tools=tool_schemas(),
            tool_choice="auto",
            on_content_delta=on_delta,
            enable_thinking=False,
            max_tokens=MAX_ANSWER_TOKENS,
        ),
        timeout=MODEL_TIMEOUT_SECONDS,
    )


def _make_client() -> Any:
    from app.llm import LLMClient

    return LLMClient(timeout=MODEL_TIMEOUT_SECONDS, retries=1)


async def _turn(pipeline_id: str, owner_id: str, text: str, selected: Optional[str], emit: Any) -> None:
    pipeline = service.load(pipeline_id)
    with get_db() as conn:
        repo.add_message(conn, pipeline_id, "user", text, {"selected_node": selected} if selected else {})
    before = copy.deepcopy(pipeline["draft"])
    editor = GraphEditor(pipeline["draft"], _file_paths(pipeline_id))
    run = _latest_run(pipeline_id)
    system = agent_context.catalog_text() + "\n\n" + agent_context.state_text(
        name=pipeline["name"], graph=editor.graph, files=editor.files, selected=selected, run=run
    )
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(_history_for_model(pipeline_id)[:-1])  # the user's message is added last, below
    messages.append({"role": "user", "content": text})

    actions: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {}
    answer_parts: List[str] = []
    failure: Optional[str] = None
    client = _make_client()

    from app.llm import clear_usage_context, set_usage_context

    token = set_usage_context(session_id=f"pipeline_{pipeline_id}", phase="pipeline", call_purpose="pipeline_agent")
    try:
        for _ in range(MAX_ITERATIONS):
            result = await _model_step(client, messages, emit)
            content = (result.content or "").strip()
            if content:
                answer_parts.append(content)
            calls = list(result.tool_calls or [])
            if not calls:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments or {}, ensure_ascii=False)},
                        }
                        for call in calls
                    ],
                }
            )
            graph_touched = False
            for call in calls:
                args = _call_arguments(call.arguments)
                outcome = _apply(editor, pipeline_id, call.name, args, state)
                summary = _summary(call.name, args, outcome)
                actions.append({"name": call.name, "summary": summary, "ok": bool(outcome.get("ok")), **({} if outcome.get("ok") else {"error": outcome.get("error")})})
                await emit({"type": "tool", "name": call.name, "summary": summary, "ok": bool(outcome.get("ok")), "error": outcome.get("error")})
                if call.name not in {"validate", "read_output", "request_run"}:
                    graph_touched = graph_touched or bool(outcome.get("ok"))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps(outcome, ensure_ascii=False)[:12000]}
                )
            if graph_touched:
                saved = await asyncio.to_thread(service.update, pipeline_id, graph=editor.graph)
                await emit({"type": "graph", "graph": saved["draft"], "validation": saved["validation"]})
            if state.get("run_request"):
                await emit({"type": "run_request", **state["run_request"]})
        else:
            answer_parts.append("(I stopped after many steps. Ask me to continue if something is missing.)")
    except asyncio.TimeoutError:
        failure = "the model took too long to answer"
    except Exception as exc:  # noqa: BLE001 - the user gets the reason, the log gets the traceback
        logger.exception("pipeline agent turn failed")
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        clear_usage_context(token)

    answer = "\n\n".join(answer_parts).strip()
    if failure:
        answer = (answer + "\n\n" if answer else "") + f"(The assistant could not finish: {failure})"
    changed = any(item["ok"] and item["name"] not in {"validate", "read_output", "request_run"} for item in actions)
    meta: Dict[str, Any] = {"actions": actions}
    if changed:
        meta["before_graph"] = before
    if state.get("run_request"):
        meta["run_request"] = state["run_request"]
    with get_db() as conn:
        message_id = repo.add_message(conn, pipeline_id, "assistant", answer or "(no answer)", meta)
    public = {**meta, "can_undo": changed}
    public.pop("before_graph", None)
    if failure:
        await emit({"type": "error", "message": failure})
    await emit({"type": "done", "message": {"id": message_id, "role": "assistant", "content": answer, "meta": public}})


def start_turn(pipeline_id: str, owner_id: str, text: Any, selected_node: Any = None) -> AsyncIterator[Dict[str, Any]]:
    """Validate the message, start the turn and return its events. A refusal raises before anything starts.

    Leaving the event loop early does not stop the turn.
    """
    message = str(text or "").strip()
    if not message:
        raise service.PipelineError(422, "write a message first")
    if len(message) > MAX_MESSAGE_CHARS:
        raise service.PipelineError(413, f"the message is longer than {MAX_MESSAGE_CHARS} characters")
    if busy(pipeline_id):
        raise service.PipelineError(409, "the agent is still working on the previous message")
    selected = str(selected_node) if selected_node else None
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: Dict[str, Any]) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            await _turn(pipeline_id, owner_id, message, selected, emit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("pipeline agent crashed")
            await emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(None)

    task = asyncio.get_running_loop().create_task(runner())
    _running[pipeline_id] = task
    _background.add(task)
    task.add_done_callback(_background.discard)

    async def events() -> AsyncIterator[Dict[str, Any]]:
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event

    return events()


# ------------------------------------------------------------------ undo and run confirmation
def undo(pipeline_id: str, message_id: int) -> Dict[str, Any]:
    if busy(pipeline_id):
        raise service.PipelineError(409, "the agent is working; wait for it to finish")
    with get_db() as conn:
        message = repo.get_message(conn, pipeline_id, message_id)
        latest_changing = None
        for item in reversed(repo.list_messages(conn, pipeline_id)):
            if (item.get("meta") or {}).get("before_graph") and not (item.get("meta") or {}).get("undone"):
                latest_changing = item["id"]
                break
    meta = (message or {}).get("meta") or {}
    if not message or not meta.get("before_graph") or meta.get("undone"):
        raise service.PipelineError(404, "there is nothing to undo for this message")
    if latest_changing != message_id:
        raise service.PipelineError(409, "only the latest change can be undone")
    saved = service.update(pipeline_id, graph=meta["before_graph"])
    meta["undone"] = True
    with get_db() as conn:
        repo.update_message_meta(conn, message_id, meta)
    return {"draft": saved["draft"], "validation": saved["validation"]}


def resolve_run_request(pipeline_id: str, owner_id: str, message_id: int, decision: str) -> Dict[str, Any]:
    """The user's answer to the agent's request to run: ``run`` starts the pipeline, ``dismiss`` drops it."""
    with get_db() as conn:
        message = repo.get_message(conn, pipeline_id, message_id)
    meta = (message or {}).get("meta") or {}
    request = meta.get("run_request")
    if not request:
        raise service.PipelineError(404, "this message has no run request")
    if request.get("state") != "pending":
        raise service.PipelineError(409, "this request was already answered")
    if decision not in {"run", "dismiss"}:
        raise service.PipelineError(422, "decision must be run or dismiss")
    run = None
    if decision == "run":
        run = service.start_run(pipeline_id, owner_id, None)
    request["state"] = "approved" if decision == "run" else "dismissed"
    meta["run_request"] = request
    with get_db() as conn:
        repo.update_message_meta(conn, message_id, meta)
    return {"state": request["state"], "run": run}


__all__ = ["busy", "clear", "history", "resolve_run_request", "start_turn", "undo"]
