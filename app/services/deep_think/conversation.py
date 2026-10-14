"""Explicit task-local continuation, following Hermes' messages-in/messages-out loop."""
from copy import deepcopy
from typing import Any


def _validate_messages(messages: Any, protocol: str) -> None:
    if not isinstance(messages, list) or not messages:
        raise ValueError("Execution continuation has no messages")
    pending = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError("Invalid execution message")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"} or (role == "system" and index):
            raise ValueError("Invalid execution message role")
        calls = message.get("tool_calls") or []
        if protocol == "strict" and (calls or role == "tool"):
            raise ValueError("Execution continuation protocol changed")
        if pending and role != "tool":
            raise ValueError("Execution continuation has unsettled tool calls")
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else None
            if role != "assistant" or not call_id or call_id in pending:
                raise ValueError("Invalid execution tool call")
            pending.add(call_id)
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise ValueError("Execution continuation has an orphan tool result")
            pending.remove(call_id)
    if pending:
        raise ValueError("Execution continuation has unsettled tool calls")


def seed_messages(context, *, system_prompt, user_message, protocol, history=()):
    continuation = context.get("execution_continuation")
    if continuation is None:
        return [{"role": "system", "content": system_prompt}, *history,
                {"role": "user", "content": user_message}]
    scope = context.get("execution_history_scope")
    if (not scope or not isinstance(continuation, dict)
            or continuation.get("scope") != scope or continuation.get("protocol") != protocol):
        raise ValueError("Execution continuation belongs to a different task or contract")
    messages = continuation.get("messages")
    _validate_messages(messages, protocol)
    messages = deepcopy(messages)
    if messages[0].get("role") == "system":
        messages[0] = {"role": "system", "content": system_prompt}
    else:
        messages.insert(0, {"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})
    return messages


def produced_paths(context):
    return list((context.get("execution_continuation") or {}).get("produced_paths") or [])


def capture(context, *, messages, final_answer, protocol, produced=()):
    scope = context.get("execution_history_scope")
    if not scope:
        return None
    # A settled transcript is context, never a queue to redispatch.
    _validate_messages(messages, protocol)
    messages = deepcopy(messages)
    if final_answer and (messages[-1].get("role") != "assistant"
                         or messages[-1].get("content") != final_answer):
        messages.append({"role": "assistant", "content": final_answer})
    return {"version": 1, "scope": deepcopy(scope), "protocol": protocol,
            "messages": messages, "produced_paths": list(produced)}
