"""Persist native controller boundaries and fenced tool observations."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.chat_run_state import chat_run_claim
from app.services.deep_think.models import ThinkingStep
from app.services.run_budget import current_run_budget

logger = logging.getLogger(__name__)


class ControllerRestoreError(RuntimeError):
    """Runtime binding could not be restored; further execution must stop."""


async def restore_runtime_state(agent: Any, state: dict) -> None:
    """Restore only local/controller state, without dispatching another tool.

    This callback deliberately bypasses safe_callback: stale outer bindings
    must never turn into another write after a suppressed restoration error.
    """
    from app.services.run_budget import check_run_active

    check_run_active()
    if 'created_plan_this_turn_id' in state:
        agent._created_plan_this_turn_id = state['created_plan_this_turn_id']
    callback = getattr(agent, 'on_runtime_restore', None)
    if callback is None:
        return
    try:
        result = callback(state)
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except ControllerRestoreError:
        raise
    except Exception as exc:
        from app.services.run_budget import RunDeadlineExceeded
        if isinstance(exc, RunDeadlineExceeded):
            raise
        raise ControllerRestoreError('Controller runtime binding restoration failed') from exc


def namespace(agent: Any, user_query: str, task_context: Any = None) -> str:
    profile = agent.request_profile or {}
    task = getattr(task_context, "task_id", None) or profile.get("current_task_id") or profile.get("task_id") or 0
    plan = profile.get("current_plan_id") or profile.get("plan_id") or 0
    digest = hashlib.sha256(user_query.encode()).hexdigest()[:16]
    return f"native:{plan}:{task}:{digest}"


def checkpoint_key(agent: Any, user_query: str, task_context: Any = None) -> str:
    digest = hashlib.sha256(user_query.encode()).hexdigest()
    task = getattr(task_context, "task_id", None)
    plan = (agent.request_profile or {}).get("current_plan_id") or (agent.request_profile or {}).get("plan_id") or 0
    return f"task:{plan}:{task}:{digest}" if task is not None else f"chat:{digest}"


def load_native_checkpoint(ledger: Any, key: str):
    from app.services.execution.step_ledger import LedgerCancelled
    from app.services.run_budget import RunDeadlineExceeded

    try:
        return ledger.load_checkpoint(checkpoint_key=key)
    except (LedgerCancelled, RunDeadlineExceeded):
        raise
    except Exception as exc:
        raise ControllerRestoreError("The controller checkpoint cannot be restored") from exc


def restore_checkpoint(agent: Any, checkpoint: Any, user_query: str, task_context: Any, context: dict):
    """An explicit continuation must never silently turn into a fresh execution."""
    agent._checkpoint_query_sha256 = hashlib.sha256(user_query.encode()).hexdigest()
    agent._checkpoint_task_id = getattr(task_context, "task_id", None)
    if checkpoint is None:
        if context.get("resume_from_run_id"):
            if agent._checkpoint_task_id is not None and context.get("_resume_new_scope_verified") is True:
                return None
            raise ControllerRestoreError("The requested controller checkpoint is unavailable; reconciliation is required")
        return None
    state = checkpoint.controller_state
    if state.get("engine")=="delegate":
        raise ControllerRestoreError("Cannot restore a delegated operation into a native model loop")
    if context.get("resume_from_run_id"):
        if (state.get("query_sha256") != agent._checkpoint_query_sha256
                or state.get("task_id") != agent._checkpoint_task_id):
            raise ControllerRestoreError("Checkpoint request/task mismatch; reconciliation is required")
        agent._checkpoint_namespace = state["namespace"]
        if "bound_plan_id" in state:
            agent.request_profile["current_plan_id"] = state["bound_plan_id"]
            agent.request_profile["plan_id"] = state["bound_plan_id"]
        return checkpoint
    return checkpoint if state.get("namespace") == agent._checkpoint_namespace else None


def task_scope_start_allowed(agent: Any, context: dict) -> bool:
    """Allow a new task scope only with evidence that no unknown effect is lost."""
    from app.repository.run_steps import list_checkpoint_pointers, list_steps
    from app.services.execution.step_ledger import ResultUnavailable

    ledger = ledger_for(agent)
    if ledger is None:
        return False
    scope_prefix = agent._checkpoint_key.rsplit(":", 1)[0] + ":"
    step_prefix = agent._checkpoint_namespace.rsplit(":", 1)[0] + ":"
    pointers = [row for row in list_checkpoint_pointers(ledger.run_id)
                if row["checkpoint_key"].startswith(scope_prefix)]
    steps = [step for step in list_steps(ledger.run_id) if step.key.tool_call_id.startswith(step_prefix)]
    if not pointers:
        return context.get("resume_scope_entered") is False and not steps
    # A completed controller may start a new repair query. An interrupted
    # controller with a different query must reconcile its original scope.
    for pointer in pointers:
        if load_native_checkpoint(ledger, pointer["checkpoint_key"]).phase != "native_final":
            return False
    latest = {}
    for step in steps:
        identity = (step.key.tool_call_id, step.key.params_fingerprint)
        if identity not in latest or step.key.attempt > latest[identity].key.attempt:
            latest[identity] = step
    for step in latest.values():
        if step.replay_policy in {"read_only", "idempotent"}:
            continue
        if step.status != "succeeded":
            return False
        try:
            ledger.load_result(step)
        except ResultUnavailable:
            return False
    return True


def ensure_plan_resume_scope(plan_id: int, task_id: int, user_query: str, *, previously_entered: bool) -> None:
    """Validate an inherited plan continuation before touching its attempt marker."""
    from types import SimpleNamespace

    task = SimpleNamespace(task_id=task_id)
    agent = SimpleNamespace(request_profile={"current_plan_id": plan_id})
    agent._checkpoint_namespace = namespace(agent, user_query, task)
    agent._checkpoint_key = checkpoint_key(agent, user_query, task)
    ledger = ledger_for(agent)
    if ledger is not None and load_native_checkpoint(ledger, agent._checkpoint_key) is not None:
        return
    if not task_scope_start_allowed(agent, {"resume_scope_entered": previously_entered}):
        raise ControllerRestoreError("This previously entered task has no usable continuation scope; reconciliation is required")


def ledger_for(agent: Any):
    claim = chat_run_claim.get()
    if claim is None:
        return None
    ledger = getattr(agent, "_step_ledger", None)
    if ledger is None or ledger.run_id != claim[0]:
        from app.services.execution.step_ledger import StepLedger

        ledger = StepLedger(claim[0], worker_id=claim[1])
        agent._step_ledger = ledger
    return ledger


def json_value(value: Any) -> Any:
    """Controller state has no services, callbacks or transport objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(item) for item in value]
    raise TypeError(f"Unsupported checkpoint value: {type(value).__name__}")


def pack_result(result: Any) -> dict:
    from app.services.execution.step_ledger import params_fingerprint

    return {
        "finish_reason": getattr(result,"finish_reason",None),
        "usage": getattr(result,"usage",None),
        "content": result.content or "",
        "tool_calls": [
            {
                "id": call.id, "name": call.name, "arguments": json_value(call.arguments),
                "params_fingerprint": params_fingerprint(call.name, call.arguments or {}),
            }
            for call in result.tool_calls
        ],
    }


def unpack_result(raw: dict):
    from app.llm import NativeStreamResult, NativeToolCall
    from app.services.execution.step_ledger import params_fingerprint

    calls = []
    for item in raw.get("tool_calls") or []:
        arguments = item.get("arguments") or {}
        if item.get("params_fingerprint") != params_fingerprint(item["name"], arguments):
            raise ControllerRestoreError("Checkpoint tool arguments changed; reconciliation is required")
        calls.append(NativeToolCall(id=item.get("id") or "", name=item["name"], arguments=arguments))
    return NativeStreamResult(content=raw.get("content") or "", tool_calls=calls,finish_reason=raw.get("finish_reason"),usage=raw.get("usage"))


def restore_steps(raw_steps: list[dict]) -> list[ThinkingStep]:
    names = {field.name for field in dataclasses.fields(ThinkingStep)}
    steps = []
    try:
        for raw in raw_steps:
            values = {key: value for key, value in raw.items() if key in names}
            for key in ("timestamp", "started_at", "finished_at"):
                if isinstance(values.get(key), str):
                    values[key] = datetime.fromisoformat(values[key])
            steps.append(ThinkingStep(**values))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ControllerRestoreError("Invalid thinking-step checkpoint") from exc
    return steps


def restore_cycle(cycle: Any, checkpoint: Any) -> None:
    """Keep execution/probe state; a user-requested child run has a new work budget."""
    raw = checkpoint.controller_state.get("cycle") or {}
    for field in dataclasses.fields(cycle):
        if field.name in {"runtime_iteration_limit", "base_iteration_limit"} or field.name not in raw:
            continue
        value, default = raw[field.name], getattr(cycle, field.name)
        valid = (
            (default is None and (value is None or isinstance(value, (int, str))))
            or (type(value) is type(default))
            or (isinstance(default, float) and type(value) in {int, float})
        )
        if not valid:
            raise ControllerRestoreError(f"Invalid controller checkpoint field: {field.name}")
        setattr(cycle, field.name, value)


def restore_guard(guard: dict, checkpoint: Any) -> None:
    raw = checkpoint.controller_state.get("guard") or {}
    if not isinstance(raw, dict):
        raise ControllerRestoreError("Invalid guard checkpoint")
    for key, value in raw.items():
        if key in {"started_at", "acceptance_spec", "expected_outputs"}:
            continue
        if key == "failure_sig_warned":
            if not isinstance(value, (list, set)):
                raise ControllerRestoreError("Invalid guard warning checkpoint")
            value = set(value)
        if key in guard and type(value) is not type(guard[key]):
            raise ControllerRestoreError(f"Invalid guard checkpoint field: {key}")
        guard[key] = value


def unresolved_execution_issues(agent: Any, ledger: Any, issues: list[dict]) -> list[dict]:
    """A reconciled observation may clear its issue; mere file existence cannot."""
    from app.repository.run_steps import list_steps
    from app.services.execution.step_ledger import ResultUnavailable

    steps = list_steps(ledger.run_id)
    remaining = []
    for issue in issues:
        matches = [step for step in steps if step.key.tool_call_id == issue.get("step")
                   and step.key.params_fingerprint == issue.get("params_fingerprint")]
        latest = max(matches, key=lambda step: step.key.attempt) if matches else None
        if latest is not None and latest.status == "succeeded":
            try:
                result = ledger.load_result(latest)
                success, _ = agent._normalize_tool_callback_outcome(result.get("tool_result"))
                if success:
                    continue
            except ResultUnavailable:
                pass
        remaining.append(issue)
    return remaining


async def save_native_checkpoint(
    agent: Any, *, phase: str, iteration: int, messages: list[dict],
    steps: list[ThinkingStep], tools_used: list[str], cycle: Any,
    guard_state: dict, pending_result: Any = None,
) -> None:
    ledger = ledger_for(agent)
    if ledger is None:
        return
    from app.services.execution.step_ledger import ControllerCheckpoint, RemainingBudget

    budget = current_run_budget()
    guard = {key: value for key, value in guard_state.items() if key not in {"started_at", "acceptance_spec"}}
    spec = getattr(agent, "_acceptance_spec", None)
    state = {
        "namespace": agent._checkpoint_namespace,
        "query_sha256": agent._checkpoint_query_sha256,
        "task_id": agent._checkpoint_task_id,
        "bound_plan_id": agent._current_plan_id(),
        "bound_plan_title": (agent.request_profile or {}).get("current_plan_title") or (agent.request_profile or {}).get("plan_title"),
        "created_plan_this_turn_id": getattr(agent, '_created_plan_this_turn_id', None),
        "cycle": json_value(cycle), "guard": json_value(guard),
        "thinking_steps": json_value(steps), "tools_used": list(tools_used),
        "schema_policy": 2 if getattr(agent._schema_disclosure,"v2",False) else 1,
        "schema_disclosed": sorted(getattr(agent._schema_disclosure,"_disclosed",set())),
        "schema_loaded": sorted(getattr(agent, "_schema_disclosure").loaded),
        "output_spec": spec.to_dict() if spec is not None else None,
        "output_input_snapshot": json_value(getattr(agent, "_output_input_snapshot", {})),
        "output_spec_base_dir": getattr(agent, "_acceptance_base_dir", None),
        "execution_issues": json_value(getattr(agent, "_execution_issues", [])),
        "produced_image_paths": list(getattr(agent, "_produced_image_paths", [])),
        "pending_result": pack_result(pending_result) if pending_result is not None else None,
    }
    checkpoint = ControllerCheckpoint(
        run_id=ledger.run_id, phase=phase, iteration=iteration,
        messages=json_value(messages), controller_state=state,
        control_counters={"iteration": iteration},
        remaining_budget=RemainingBudget(
            iterations=max(0, cycle.runtime_iteration_limit - iteration),
            seconds=budget.remaining_seconds(closeout=True) if budget else None,
        ),
    )
    await asyncio.to_thread(ledger.save_checkpoint, checkpoint, checkpoint_key=agent._checkpoint_key)


def replay_policy(agent: Any, name: str, params: dict) -> str:
    if name == "load_tool_schema":
        return "idempotent"  # disclosure is a repeatable local set update
    if name == "file_operations":
        return "read_only" if str(params.get("operation") or "read").lower() in {
            "read", "list", "exists", "info", "profile", "census",
        } else "mutating"
    from tool_box.tools import get_tool_registry

    definition = get_tool_registry().get_tool(name)
    return "read_only" if definition is not None and definition.is_read_only else "mutating"


async def execute_recorded_tool(
    agent: Any, call: Any, iteration: int, index: int,
    execute: Callable[[], Awaitable[dict]],
) -> dict:
    ledger = ledger_for(agent)
    if ledger is None:
        return await execute()
    slot = f"{getattr(agent, '_checkpoint_namespace', 'native')}:{iteration}:{index}:{call.id or 'tool'}"
    decision = await asyncio.to_thread(
        ledger.prepare, slot, call.name, call.arguments or {},
        replay_policy=replay_policy(agent, call.name, call.arguments or {}),
    )
    if decision.action == "execute" and call.name == "execute_code" and getattr(agent, "_replayed_python_state_lost", False):
        payload = {"success": False, "error": "python_state_rebuild_required", "summary": (
            "This pending cell was not executed because replay did not restore Python variables. "
            "Plan a new cell that reloads verified files and reconstructs its inputs first."
        )}
        return {"index": index, "tool_call_id": call.id, "tool_name": call.name,
                "tool_params": call.arguments or {}, "tool_result": payload,
                "tool_result_text": json.dumps(payload), "evidence": []}
    if decision.action == "execute" and getattr(agent, "_execution_issues", []) and replay_policy(agent, call.name, call.arguments or {}) != "read_only":
        payload = {"success": False, "error": "step_reconciliation_required", "summary": (
            "This call was not executed: reconcile the uncertain prior mutation before starting more writes."
        )}
        return {"index": index, "tool_call_id": call.id, "tool_name": call.name,
                "tool_params": call.arguments or {}, "tool_result": payload,
                "tool_result_text": json.dumps(payload), "evidence": []}
    if decision.action == "replay":
        logger.info("[RUN_STEP] replay run=%s slot=%s", ledger.run_id, slot)
        replayed = dict(decision.result)
        payload = replayed.get("tool_result")
        if call.name == "execute_code" and isinstance(payload, dict):
            agent._replayed_python_state_lost = True
            payload = dict(payload)
            payload.pop("kernel", None)
            payload["hint"] = (
                "Confirmed files/observations were replayed; Python variables are not restored by replay. "
                "Reload verified files before using previous variables; do not repeat external mutations."
            )
            replayed["tool_result"] = payload
            replayed["tool_result_text"] = agent._build_tool_result_text_for_llm(
                tool_name=call.name, result=payload, success=True, error=None,
            )
        if call.name == "plan_operation" and isinstance(payload, dict):
            operation = str(payload.get('operation') or (call.arguments or {}).get('operation') or '').lower()
            if (operation in {'bind', 'create'} and payload.get('success') and payload.get('plan_id') is not None
                    and not (operation == 'create' and payload.get('binding_skipped'))):
                plan_id = payload['plan_id']
                title = payload.get('plan_title') or payload.get('title')
                agent.request_profile.update({'current_plan_id': plan_id, 'plan_id': plan_id})
                if title:
                    agent.request_profile.update({'current_plan_title': title, 'plan_title': title})
                created = getattr(agent, '_created_plan_this_turn_id', None)
                if operation == 'create' and not payload.get('reused_existing'):
                    created = plan_id
                await restore_runtime_state(agent, {'bound_plan_id': plan_id, 'bound_plan_title': title,
                    'created_plan_this_turn_id': created, 'restored_operation': operation})
        if call.name == "load_tool_schema":
            agent._schema_disclosure.record_load(str((call.arguments or {}).get("name") or ""))
        success, error = agent._normalize_tool_callback_outcome(payload)
        if agent.on_tool_result:
            await agent._safe_generic_callback(agent.on_tool_result, call.name, {
                "success": success, "error": error, "result": payload,
                "summary": agent._build_tool_callback_summary(payload),
                "iteration": iteration, "attempt": decision.step.key.attempt,
                "replayed": True,
            })
        await agent._emit_artifacts(call.name, payload, iteration)
        return replayed
    if decision.action != "execute" or not await asyncio.to_thread(ledger.claim, decision.step.key):
        error = "step_reconciliation_required" if decision.action == "reconcile" else "step_busy"
        agent._execution_issues = [*getattr(agent, "_execution_issues", []), {
            "code": error, "tool": call.name, "step": slot,
            "params_fingerprint": decision.step.key.params_fingerprint, "reason": decision.reason,
        }]
        payload = {"success": False, "error": error, "summary": (
            "The previous call may have taken effect. Reconcile its existing outputs or remote job before submitting it again."
        )}
        return {
            "index": index, "tool_call_id": call.id, "tool_name": call.name,
            "tool_params": call.arguments or {}, "tool_result": payload,
            "tool_result_text": json.dumps(payload), "evidence": [],
        }
    try:
        result = await execute()
        success, _error = agent._normalize_tool_callback_outcome(result.get("tool_result"))
        if success:
            paths = agent._extract_explicit_artifact_paths(result.get("tool_result"))
            refs = []
            base = Path(getattr(agent, "_acceptance_base_dir", "."))
            for path in paths:
                if str(path).startswith(("https://", "http://")):
                    continue  # a remote job/URL is an observation, not a local file
                candidate = Path(path)
                if not candidate.is_absolute():
                    candidate = base / candidate
                if candidate.is_dir():
                    continue
                refs.append(candidate)  # missing declared outputs must not disappear
            from app.services.run_budget import RunDeadlineExceeded

            try:
                await asyncio.to_thread(ledger.complete, decision.step.key, json_value(result), output_refs=refs)
            except RunDeadlineExceeded:
                raise
            except Exception as exc:
                agent._execution_issues = [*getattr(agent, "_execution_issues", []), {
                    "code": "step_reconciliation_required", "tool": call.name,
                    "step": slot, "params_fingerprint": decision.step.key.params_fingerprint,
                    "reason": f"Result confirmation failed: {type(exc).__name__}",
                }]
                await asyncio.to_thread(ledger.interrupt, decision.step.key, error_code="result_unavailable")
                payload = {"success": False, "error": "step_reconciliation_required", "summary": (
                    "The call returned, but its result/output could not be confirmed. Reconcile the existing effect before retrying."
                )}
                result = {**result, "tool_result": payload, "tool_result_text": json.dumps(payload)}
        else:
            await asyncio.to_thread(ledger.fail, decision.step.key, error_code="tool_failed")
        return result
    except BaseException:
        try:
            await asyncio.to_thread(ledger.interrupt, decision.step.key, error_code="interrupted")
        except Exception:
            logger.warning("[RUN_STEP] interruption could not be persisted run=%s", ledger.run_id)
        raise


async def finalize_output_spec(agent: Any, user_query: str, answer: str) -> tuple[str, dict]:
    from app.services.deep_think.acceptance import acceptance_missing

    missing = await asyncio.to_thread(
        acceptance_missing, agent, getattr(agent, "_expected_outputs_current", []) or [],
        getattr(agent, "_produced_deliverable_paths", []) or [],
    )
    agent._acceptance_missing = missing
    spec = getattr(agent, "_acceptance_spec", None)
    report = getattr(agent, "_output_verification", None)
    if report and report.get("authoritative") and report.get("status") == "failed":
        labels = ", ".join(missing) or "required outputs"
        chinese = any("\u3400" <= char <= "\u9fff" for char in user_query)
        notice = f"本轮交付验收未通过，尚缺或不符合要求：{labels}。" if chinese else f"Output acceptance did not pass: {labels}."
        answer = notice + "\n\n" + (answer or "")
    if getattr(agent, "_execution_issues", []):
        chinese = any("\u3400" <= char <= "\u9fff" for char in user_query)
        notice = ("本轮未完成：存在结果不确定的工具步骤，需要先核对已有输出或远端任务，才能继续。"
                  if chinese else "This run is incomplete: reconcile uncertain tool steps against existing outputs or remote jobs before continuing.")
        answer = notice + "\n\n" + (answer or "")
    return answer, {
        "output_spec": spec.to_dict() if spec is not None else None,
        "output_input_snapshot": getattr(agent, "_output_input_snapshot", {}),
        "output_spec_base_dir": getattr(agent, "_acceptance_base_dir", None),
        "output_verification": report,
        "execution_issues": getattr(agent, "_execution_issues", []),
    }
