"""Operation and tool names the models guess for bio_tools: the refusal says what they meant and how to call it.

On live the models called ``phabox2``/``genomad``/``checkv``/``iphop``/``fastani`` with ``run``, ``mmseqs2`` with
``cluster``/``search``, ``phylo`` with ``align``/``tree``. The refusal listed the operation names only, so the next
call often guessed again.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from tool_box.bio_tools import native_exec, operation_hints

handler = importlib.import_module("tool_box.bio_tools.bio_tools_handler")
CONFIG = native_exec.load_tools_config(str(Path(native_exec.__file__).parent / "tools_config.json"))


def _ops(tool):
    return list(CONFIG[tool]["operations"])


@pytest.mark.parametrize(
    "tool,guess,first",
    [
        ("phabox2", "run", "end_to_end"),
        ("genomad", "run", "end_to_end"),
        ("checkv", "run", "end_to_end"),
        ("iphop", "run", "predict"),
        ("fastani", "run", "compare"),
        ("fastani", "ani", "compare"),
        ("mmseqs2", "cluster", "easy_linclust"),
        ("mmseqs2", "search", "easy_search"),
        ("phylo", "align", "mafft_align"),
        ("phylo", "tree", "fasttree"),
        ("phabox2", "phager", "phamer"),
    ],
)
def test_a_guessed_operation_gets_the_one_it_meant(tool, guess, first):
    suggested = operation_hints.suggest_operations(CONFIG, tool, guess, _ops(tool))
    assert suggested and suggested[0] == first and len(suggested) <= 3


def test_an_operation_with_nothing_close_gets_no_suggestion():
    assert operation_hints.suggest_operations(CONFIG, "seqkit", "fx2tab", _ops("seqkit")) == []


@pytest.mark.parametrize("given", ["End-To-End", "end to end", "phabox2.end_to_end", "END_TO_END"])
def test_a_different_spelling_of_an_operation_is_accepted(given):
    assert operation_hints.resolve_operation(given, "phabox2", _ops("phabox2")) == "end_to_end"


def test_a_different_word_is_not_accepted_as_an_operation():
    assert operation_hints.resolve_operation("run", "phabox2", _ops("phabox2")) is None


def test_the_refusal_names_the_operation_meant_and_how_to_call_it():
    result = asyncio.run(handler.bio_tools_handler(tool_name="genomad", operation="run", input_file="/tmp/x.fa"))
    assert result["success"] is False and result["error_code"] == "unknown_operation"
    assert result["suggested_operations"][0] == "end_to_end"
    assert "Did you mean 'end_to_end'" in result["error"] and "Usage: genomad.end_to_end" in result["error"]
    assert set(result["available_operations"]) == set(_ops("genomad"))
    assert all(result["available_operations"].values())
    hint = result["retry_hint"]
    assert hint["tool_name"] == "genomad" and hint["operation"] == "end_to_end"
    assert "input_file" in hint


def test_a_refusal_without_a_close_match_still_lists_every_operation():
    result = asyncio.run(handler.bio_tools_handler(tool_name="seqkit", operation="fx2tab"))
    assert result["error_code"] == "unknown_operation" and result["suggested_operations"] == []
    assert "retry_hint" not in result
    for op in _ops("seqkit"):
        assert op in result["error"]
    assert "execute_code" in result["error"]


def test_a_different_spelling_runs_the_declared_operation():
    result = asyncio.run(handler.bio_tools_handler(tool_name="seqkit", operation="Stats"))
    assert result["error_code"] == "missing_input_file" and result["operation"] == "stats"


@pytest.mark.parametrize("operation", ["help", "stats"])
def test_an_unknown_tool_gets_the_tool_it_meant(operation):
    result = asyncio.run(handler.bio_tools_handler(tool_name="virsorter", operation=operation))
    assert result["success"] is False and result["error_code"] == "unknown_tool"
    assert result["error"].startswith("Unknown tool: virsorter")
    assert result["suggested_tools"] == ["virsorter2"] and "virsorter2" in result["available_tools"]


def test_the_chat_layer_keeps_the_operation_details():
    from app.routers.chat.tool_results import sanitize_tool_result

    raw = {
        "success": False, "tool": "phabox2", "operation": "run", "error": "Unknown operation 'run' for phabox2.",
        "error_code": "unknown_operation", "available_operations": {"end_to_end": "all"},
        "suggested_operations": ["end_to_end"], "retry_hint": {"tool_name": "phabox2"},
        "available_tools": ["phabox2"], "suggested_tools": ["phabox2"],
    }
    kept = sanitize_tool_result("bio_tools", raw)
    for key in ("available_operations", "suggested_operations", "retry_hint", "available_tools", "suggested_tools"):
        assert kept[key] == raw[key], key


def test_the_native_server_suggests_only_operations_it_can_run(monkeypatch, tmp_path):
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    monkeypatch.setattr(handler, "_native_runtime_dir", lambda: str(tmp_path))
    result = asyncio.run(handler.bio_tools_handler(tool_name="phabox2", operation="run", input_file="/tmp/x.fa"))
    assert result["error_code"] == "unknown_operation"
    assert result["suggested_operations"][0] == "end_to_end"
    assert set(result["available_operations"]) <= set(native_exec.native_catalog(CONFIG)["phabox2"])
