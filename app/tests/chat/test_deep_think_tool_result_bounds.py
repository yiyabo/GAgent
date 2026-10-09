"""Bound the tool-result text handed to the prompt for non-whitelisted tools.

``_build_tool_result_text_for_llm`` compacts file_operations / phagescope_research
/ code_executor / execute_code through dedicated per-tool compactors. Every other
tool (vision_reader, document_reader, literature_pipeline, url_fetch, ...) had no
branch: an oversized result was returned whole, so ``MAX_TOOL_RESULT_TEXT_CHARS``
was not an upper bound at all. These tests pin the head/tail fallback and the
spill pointer, and pin that the per-tool compactors are untouched.
"""

from __future__ import annotations

import json

from app.services.deep_think_agent import DeepThinkAgent

MAX = DeepThinkAgent.MAX_TOOL_RESULT_TEXT_CHARS


def _big_result() -> dict:
    return {"success": True, "text": "A" * 40_000 + "B" * 40_000, "page_count": 12}


def test_oversized_non_whitelisted_result_is_bounded() -> None:
    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="document_reader",
        result=_big_result(),
        success=True,
        error=None,
    )

    assert len(text) <= MAX
    payload = json.loads(text)
    assert payload["tool"] == "document_reader"
    assert payload["success"] is True
    assert payload["error"] is None
    # head kept (status/ids at the front) and tail kept (freshest output last)
    assert "A" * 200 in text
    assert "B" * 200 in text
    assert "truncated" in text


def test_bounded_result_names_the_persisted_spill_path() -> None:
    spill = "tool_outputs/job_7/step_2_document_reader_ab12cd/result.json"
    result = _big_result()
    result["storage"] = {
        "result_path": f"/srv/runtime/sessions/s-1/{spill}",
        "relative": {"result_path": spill},
    }

    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="document_reader",
        result=result,
        success=True,
        error=None,
    )

    assert len(text) <= MAX
    assert "full output persisted at" in text
    assert "result.json" in text
    # Guidance names a tool that really exists and whose read op really accepts a
    # path (the quotes are JSON-escaped inside the serialized body).
    assert "file_operations" in text
    assert "operation=" in text
    assert '\\"read\\"' in text


def test_bounded_result_degrades_without_a_spill_path() -> None:
    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="url_fetch",
        result={"success": True, "body": "z" * 60_000},
        success=True,
        error=None,
    )

    assert len(text) <= MAX
    assert "full output not persisted" in text
    assert "instead of re-running the tool" in text


def test_oversized_error_field_is_bounded() -> None:
    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="url_fetch",
        result={"ok": True},
        success=False,
        error="E" * 80_000,
    )

    assert len(text) <= MAX
    assert "truncated" in text


def test_truncation_survives_json_escaping_heavy_payloads() -> None:
    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="url_fetch",
        result={"body": ("\n\t\"" + "x") * 30_000},
        success=True,
        error=None,
    )

    assert len(text) <= MAX
    assert "truncated" in text
    json.loads(text)  # still parseable by receipt projection / truth barriers


def test_small_non_whitelisted_result_passes_through_unchanged() -> None:
    result = {"success": True, "ok": 1}

    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="url_fetch",
        result=result,
        success=True,
        error=None,
    )

    assert json.loads(text) == {
        "success": True,
        "tool": "url_fetch",
        "result": result,
        "error": None,
    }


def test_whitelisted_compactor_output_is_unchanged() -> None:
    result = {
        "success": True,
        "status": "ok",
        "exit_code": 0,
        "output": "out" * 20_000,
        "noise_top": "drop",
    }
    compacted = DeepThinkAgent._compact_tool_result_for_llm("execute_code", result)
    assert compacted is not None

    text = DeepThinkAgent._build_tool_result_text_for_llm(
        tool_name="execute_code",
        result=result,
        success=True,
        error=None,
    )

    assert json.loads(text) == {
        "success": True,
        "tool": "execute_code",
        "result": compacted,
        "error": None,
    }
