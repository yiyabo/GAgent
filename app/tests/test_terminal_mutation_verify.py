"""Unit tests for local_mutation terminal verification helpers."""

from __future__ import annotations

import re

from app.routers.chat.terminal_mutation_verify import (
    infer_mutation_kind,
    wrap_local_mutation_command,
)


def test_wrap_appends_exit_marker():
    w = wrap_local_mutation_command("echo hi", 7)
    assert "echo hi" in w
    assert "__CODEX_EXIT__7__" in w
    assert w.endswith("\n")


def test_infer_mutation_kind():
    assert infer_mutation_kind("unzip -o '/tmp/a.zip' -d '/tmp/out'") == "extract"
    assert infer_mutation_kind("rm -rf '/tmp/x'") == "delete"
    assert infer_mutation_kind("cp 'a' 'b'") == "copy"
    assert infer_mutation_kind("mv 'a' 'b'") == "move"


def test_exit_marker_regex():
    text = "done\n__CODEX_EXIT__3__0\n"
    m = re.search(r"__CODEX_EXIT__(\d+)__(-?\d+)", text)
    assert m is not None
    assert m.group(1) == "3"
    assert m.group(2) == "0"
