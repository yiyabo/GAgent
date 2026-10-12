"""Exercise the real distiller/client boundary; only HTTP transport is fake."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import app.llm as llm
from app.database import get_db, init_db
from app.repository.llm_usage import init_llm_usage_table
from app.services.chat_run_state import chat_run_claim
from app.services.skill_learning.distiller import SkillDistiller


EVIDENCE = {"session_id": "source-session", "run_id": "source-run", "steps": []}
DRAFT = {
    "name": "checked-json-rows", "description": "生成并核对 JSON 行数",
    "domain": "routine", "when_to_use": "需要整理表格行数时",
    "inputs": ["输入数据"], "steps": [{"instruction": "生成 JSON 后独立核对行数。", "evidence_ids": ["step-1"]}],
    "verification": ["独立重算行数"], "limitations": ["只验证格式与行数"],
    "pitfalls": [], "keywords": ["JSON", "行数"],
}


def response(text: str) -> httpx.Response:
    events = [
        {"choices": [{"delta": {"content": text[:len(text)//2]}}]},
        {"choices": [{"delta": {"content": text[len(text)//2:]}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}},
    ]
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_real_distiller_records_background_parent_and_restores_context(isolated_app_env, monkeypatch):
    init_db()
    init_llm_usage_table()
    calls = []
    monkeypatch.setenv("LLM_THINKING_OFF_REASONING_EFFORT", "low")

    def provider(request):
        calls.append(json.loads(request.content))
        return response(json.dumps({"reusable": True, "reason": "来自执行证据", "draft": DRAFT}))

    # An unrelated live claim must never steal this background call's origin.
    claim = chat_run_claim.set(("unrelated-run", "unrelated-worker"))
    outer = llm.set_usage_context(session_id="outer-session", run_id="outer-run")
    original = llm._usage_context.get()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
            monkeypatch.setattr(llm, "_get_shared_async_client", lambda: transport)
            result = await SkillDistiller().distill(EVIDENCE)
        assert result.reusable and result.draft.name == DRAFT["name"]
        assert llm._usage_context.get() is original
        assert len(calls) == 1
        assert calls[0]["max_tokens"] == 3200
        assert calls[0]["reasoning_effort"] == "low"
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM llm_usage_log WHERE call_purpose='skill_distillation'").fetchall()
        assert len(rows) == 1
        assert rows[0]["session_id"] == "source-session"
        assert rows[0]["parent_run_id"] == "source-run"
        assert rows[0]["run_id"] is None
        assert rows[0]["total_tokens"] == 160
    finally:
        llm.clear_usage_context(outer)
        chat_run_claim.reset(claim)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_json", "cancelled"])
async def test_distiller_failure_restores_context_without_retry(isolated_app_env, monkeypatch, failure):
    init_db()
    init_llm_usage_table()
    calls = []

    def provider(request):
        calls.append(request)
        if failure == "cancelled":
            raise asyncio.CancelledError("test cancellation")
        return response("invalid JSON")

    outer = llm.set_usage_context(session_id="outer-session", parent_run_id="outer-parent")
    original = llm._usage_context.get()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
            monkeypatch.setattr(llm, "_get_shared_async_client", lambda: transport)
            with pytest.raises(asyncio.CancelledError if failure == "cancelled" else json.JSONDecodeError):
                await SkillDistiller().distill(EVIDENCE)
        assert llm._usage_context.get() is original
        assert len(calls) == 1
    finally:
        llm.clear_usage_context(outer)


def test_parent_attribution_preserves_legacy_claim_and_avoids_self_reference():
    claim = chat_run_claim.set(("claim-parent", "worker"))
    try:
        assert llm._usage_parent_run({"run_id": "child"}) == "claim-parent"
        assert llm._usage_parent_run({"run_id": "child", "parent_run_id": "explicit"}) == "explicit"
        assert llm._usage_parent_run({"run_id": "child", "parent_run_id": "child"}) is None
    finally:
        chat_run_claim.reset(claim)
