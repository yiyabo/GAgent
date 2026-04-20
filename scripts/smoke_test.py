#!/usr/bin/env python3
"""Automated smoke test for the chat pipeline.

Sends test prompts to POST /chat/stream, parses SSE events, and validates
routing decisions, tool calls, and response content.

Usage:
    # Start backend first:
    python -m uvicorn app.main:create_app --factory --port 9000

    # Run smoke tests:
    python scripts/smoke_test.py

    # With custom base URL:
    API_URL=http://localhost:8000 python scripts/smoke_test.py

    # Mock mode (no real LLM calls):
    LLM_MOCK=1 python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

BASE_URL = os.getenv("API_URL", "http://localhost:9000")
OWNER = os.getenv("TEST_OWNER", "smoke-tester")
HEADERS = {"X-Forwarded-User": OWNER, "Content-Type": "application/json"}
TIMEOUT = 60.0


@dataclass
class SSEResult:
    """Parsed result from a /chat/stream SSE response."""
    events: List[Dict[str, Any]] = field(default_factory=list)
    thinking_steps: List[Dict[str, Any]] = field(default_factory=list)
    final_payload: Optional[Dict[str, Any]] = None
    final_answer: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed_sec: float = 0.0


def parse_sse_stream(response: httpx.Response) -> SSEResult:
    """Parse SSE events from a streaming response."""
    result = SSEResult()
    for line in response.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        raw = line[len("data: "):]
        if raw.strip() == "[DONE]":
            break
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        result.events.append(event)
        event_type = event.get("type")
        if event_type == "thinking_step":
            result.thinking_steps.append(event.get("step", {}))
        elif event_type == "final":
            result.final_payload = event.get("payload", {})
            result.final_answer = result.final_payload.get("response", "")
            result.metadata = result.final_payload.get("metadata", {})
        elif event_type == "error":
            result.error = event.get("message", str(event))
    return result


def send_chat(
    message: str,
    *,
    session_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> SSEResult:
    """Send a chat message and return parsed SSE result."""
    body: Dict[str, Any] = {"message": message}
    if session_id:
        body["session_id"] = session_id
    if context:
        body["context"] = context

    start = time.time()
    with httpx.Client(timeout=TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/chat/stream",
            headers=HEADERS,
            json=body,
        ) as resp:
            resp.raise_for_status()
            result = parse_sse_stream(resp)
    result.elapsed_sec = time.time() - start
    return result


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.elapsed = 0.0

    def ok(self, msg: str = ""):
        self.passed = True
        self.message = msg

    def fail(self, msg: str):
        self.passed = False
        self.message = msg


def test_light_greeting() -> TestResult:
    """1.1 Light — simple greeting should respond quickly without thinking steps."""
    t = TestResult("1.1 Light greeting")
    r = send_chat("你好呀")
    t.elapsed = r.elapsed_sec
    if r.error:
        t.fail(f"Error: {r.error}")
        return t
    if not r.final_answer:
        t.fail("No final answer received")
        return t
    tier = r.metadata.get("request_tier")
    visibility = r.metadata.get("thinking_visibility")
    if tier != "light":
        t.fail(f"Expected tier=light, got {tier}")
        return t
    # thinking_steps may still arrive but visibility should be hidden
    if visibility and visibility != "hidden":
        t.fail(f"Expected thinking_visibility=hidden, got {visibility}")
        return t
    t.ok(f"Answer: {r.final_answer[:60]}... ({r.elapsed_sec:.1f}s)")
    return t


def test_standard_explanation() -> TestResult:
    """1.2 Standard — concept explanation should show thinking."""
    t = TestResult("1.2 Standard explanation")
    r = send_chat("请详细解释一下 transformer 和 RNN 在序列建模上的核心区别")
    t.elapsed = r.elapsed_sec
    if r.error:
        t.fail(f"Error: {r.error}")
        return t
    if not r.final_answer:
        t.fail("No final answer received")
        return t
    tier = r.metadata.get("request_tier")
    if tier not in ("standard", "research"):
        t.fail(f"Expected tier=standard or research, got {tier}")
        return t
    t.ok(f"Tier={tier}, answer length={len(r.final_answer)} ({r.elapsed_sec:.1f}s)")
    return t


def test_execute_unzip() -> TestResult:
    """2.1 Execute — explicit unzip command should route to execute_task."""
    t = TestResult("2.1 Execute unzip")
    r = send_chat("帮我把这些 zip 文件解压到当前目录")
    t.elapsed = r.elapsed_sec
    if r.error:
        t.fail(f"Error: {r.error}")
        return t
    intent = r.metadata.get("intent_type")
    tier = r.metadata.get("request_tier")
    if intent != "execute_task":
        t.fail(f"Expected intent=execute_task, got {intent}")
        return t
    if tier != "execute":
        t.fail(f"Expected tier=execute, got {tier}")
        return t
    t.ok(f"Intent={intent}, tier={tier} ({r.elapsed_sec:.1f}s)")
    return t


def test_chat_continuation() -> TestResult:
    """2.3 Chat — '继续说' should stay as chat."""
    t = TestResult("2.3 Chat continuation")
    r = send_chat("继续说")
    t.elapsed = r.elapsed_sec
    if r.error:
        t.fail(f"Error: {r.error}")
        return t
    intent = r.metadata.get("intent_type")
    if intent != "chat":
        t.fail(f"Expected intent=chat, got {intent}")
        return t
    t.ok(f"Intent={intent} ({r.elapsed_sec:.1f}s)")
    return t


def test_plan_creation() -> TestResult:
    """3.1 Plan creation — should call plan_operation."""
    t = TestResult("3.1 Plan creation")
    r = send_chat("/think 针对噬菌体宿主预测这个方向，制作一个 plan 给我")
    t.elapsed = r.elapsed_sec
    if r.error:
        t.fail(f"Error: {r.error}")
        return t
    if not r.final_answer:
        t.fail("No final answer received")
        return t
    # Check if plan was created (look for plan_id in metadata)
    plan_meta = r.metadata.get("structured_plan") or {}
    plan_id = plan_meta.get("plan_id") or r.metadata.get("plan_id")
    reason_codes = r.metadata.get("route_reason_codes", [])
    if "intent_plan_request" not in reason_codes:
        t.fail(f"Expected intent_plan_request in reason_codes, got {reason_codes}")
        return t
    if plan_id:
        t.ok(f"Plan created: id={plan_id} ({r.elapsed_sec:.1f}s)")
    else:
        t.ok(f"Plan request routed correctly, answer length={len(r.final_answer)} ({r.elapsed_sec:.1f}s)")
    return t


def test_remote_status_query() -> TestResult:
    """5. Remote status query — should get standard tier."""
    t = TestResult("5. Remote status query")
    r = send_chat("38619 这个任务是不是还在跑")
    t.elapsed = r.elapsed_sec
    if r.error:
        t.fail(f"Error: {r.error}")
        return t
    tier = r.metadata.get("request_tier")
    reason_codes = r.metadata.get("route_reason_codes", [])
    if tier == "light":
        t.fail(f"Expected tier != light for remote status query, got {tier}")
        return t
    t.ok(f"Tier={tier}, reasons={reason_codes} ({r.elapsed_sec:.1f}s)")
    return t


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'='*60}")
    print(f"  Smoke Test — {BASE_URL}")
    print(f"{'='*60}\n")

    # Check backend is up
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=5)
        resp.raise_for_status()
        print(f"✅ Backend healthy: {resp.json()}\n")
    except Exception as e:
        print(f"❌ Backend not reachable at {BASE_URL}: {e}")
        print("   Start it with: python -m uvicorn app.main:create_app --factory --port 9000")
        sys.exit(1)

    tests = [
        test_light_greeting,
        test_standard_explanation,
        test_execute_unzip,
        test_chat_continuation,
        test_plan_creation,
        test_remote_status_query,
    ]

    results: List[TestResult] = []
    for test_fn in tests:
        print(f"  Running: {test_fn.__doc__ or test_fn.__name__}...")
        try:
            result = test_fn()
        except Exception as e:
            result = TestResult(test_fn.__name__)
            result.fail(f"Exception: {e}")
        results.append(result)
        status = "✅" if result.passed else "❌"
        print(f"  {status} {result.name}: {result.message}\n")

    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"{'='*60}")
    print(f"  Results: {passed}/{total} passed")
    total_time = sum(r.elapsed for r in results)
    print(f"  Total time: {total_time:.1f}s")
    print(f"{'='*60}\n")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
