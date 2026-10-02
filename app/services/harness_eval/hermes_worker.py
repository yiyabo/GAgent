"""Standalone Hermes SDK worker: stdlib imports until the pinned runtime loads.

No GAgent application, corpus oracle or evaluator imports enter this process.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import signal
import sys
import time


def execute(request, *, agent_factory=None, db_factory=None, registry=None):
    if agent_factory is None:
        sys.path.insert(0, request["source_root"])
        try:
            import hermes_bootstrap  # noqa: F401 — activates the installed dependency generation
            from run_agent import AIAgent
            from hermes_state_registry import acquire
            from tools.process_registry import process_registry
            agent_factory, db_factory, registry = AIAgent, acquire, process_registry
        except BaseException as exc:
            return {"failed": True, "completed": False, "error_type": type(exc).__name__,
                    "turn_exit_reason": "runtime_import_failed", "cleanup": {"agent_created": False}}
    session_db = None
    agent = None
    result = {}
    cleanup = {"agent_closed": False, "session_db_closed": False, "registry_remaining": None}
    errors = []
    interrupted = False
    turns = []

    def stop(*_):
        nonlocal interrupted
        interrupted = True
        if agent:
            agent.hard_interrupt("evaluation deadline", tool_reason="evaluation_supervisor_cancel")

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        session_db = db_factory() if db_factory else None
        agent = agent_factory(
            base_url=os.environ["HERMES_HARNESS_BASE_URL"], api_key="isolated-evaluation",
            provider="custom", api_mode="chat_completions", model=request["model"],
            max_iterations=request["max_iterations"], max_tokens=request["max_tokens"],
            enabled_toolsets=request["toolsets"], quiet_mode=True,
            session_id=request["session_id"], session_db=session_db,
            run_budget_seconds=request["active_seconds"], cwd=request["workspace"],
            skip_context_files=True, skip_memory=True, skip_background_review=True,
            fallback_model=None, save_trajectories=False, platform="cli",
            clarify_callback=lambda questions: {"answers": {}, "outcome": "undelivered"},
        )
        history=None
        for index,turn in enumerate(request.get('turns') or [{'prompt':request['prompt'],'outputs':[]}]):
            options={'conversation_history':history} if index else {}
            raw=agent.run_conversation(turn['prompt'],task_id=request['session_id'],**options)
            if not isinstance(raw,dict):raise TypeError('Hermes result must be an object')
            files={}
            for name in turn['outputs']:
                path=Path(request['output_root'])/name
                if path.is_file():files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
            turns.append({'index':index,'status':'succeeded' if raw.get('completed') and not raw.get('failed') else 'failed',
                          'answer':raw.get('final_response') or '', 'files':files})
            if not raw.get('completed') or raw.get('failed') or raw.get('interrupted'):break
            history=raw.get('messages')
            if request.get('turns') and index+1<len(request['turns']) and not isinstance(history,list):
                raise ValueError('Hermes history missing between turns')
        if not isinstance(raw, dict):
            raise TypeError("Hermes result must be an object")
        # Full messages remain inside Hermes' isolated store; the supervisor needs only
        # its declared outcome. Provider usage is accounted at the HTTP boundary.
        keys = ("final_response", "completed", "failed", "partial", "interrupted", "turn_exit_reason", "session_id")
        result = {key: raw.get(key) for key in keys}
        if request.get('turns'):result['turn_results']=turns
    except BaseException as exc:
        result = {"failed": True, "completed": False, "error_type": type(exc).__name__}
    finally:
        # Hermes close preserves explicitly persisted background jobs. An evaluation
        # owns its entire fresh registry, so those jobs must also be terminated.
        if registry:
            try:
                for process in registry.list_sessions():
                    if process.get("status") == "running":
                        registry.kill_process(process["session_id"], source="evaluation_cleanup", consume_output=True)
            except Exception as exc:
                errors.append("registry:" + type(exc).__name__)
        if agent:
            try:
                agent.close()
                cleanup["agent_closed"] = True
            except Exception as exc:
                errors.append("agent:" + type(exc).__name__)
        if session_db:
            try:
                session_db.close()
                cleanup["session_db_closed"] = True
            except Exception as exc:
                errors.append("database:" + type(exc).__name__)
        if registry:
            try:
                cleanup["registry_remaining"] = sum(p.get("status") == "running" for p in registry.list_sessions())
            except Exception as exc:
                errors.append("remaining:" + type(exc).__name__)
        signal.signal(signal.SIGTERM, previous)
    result["interrupted"] = bool(interrupted or result.get("interrupted"))
    if request.get('turns'):result['turn_results']=turns
    result["cleanup"] = {**cleanup, "errors": errors}
    return result


def main():
    request_path = Path(sys.argv[1]).resolve()
    request = json.loads(request_path.read_text())
    started = time.monotonic()
    result = execute(request)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    path = request_path.with_name("hermes-result.json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


if __name__ == "__main__":
    main()
