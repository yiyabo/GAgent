"""Output pipeline tests: truncation, spill under session scratch, ANSI strip, hints."""

from __future__ import annotations

from pathlib import Path

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import kernel as kernel_module
from tool_box.tools_impl.execute_code import output as output_module
from tool_box.tools_impl.execute_code.config import (
    MAX_STDOUT_BYTES,
    resolve_scratch_dir,
)

import pytest


# --- unit: ANSI / truncation / hints ------------------------------------------


def test_strip_ansi_removes_escape_sequences():
    assert output_module.strip_ansi("\x1b[31mred\x1b[0m plain \x1b[1mbold\x1b[0m") == "red plain bold"


def test_truncate_stdout_under_cap_is_unchanged(tmp_path):
    text, metadata = output_module.truncate_stdout("short output", tmp_path)
    assert text == "short output"
    assert metadata["stdout_truncated"] is False
    assert metadata["stdout_bytes_total"] == len(b"short output")


def test_truncate_stdout_head_tail_split_and_spill(tmp_path):
    payload = ("H" * 30_000) + ("M" * 40_000) + ("T" * 30_000)  # 100KB total
    text, metadata = output_module.truncate_stdout(payload, tmp_path)
    assert metadata["stdout_truncated"] is True
    assert metadata["stdout_bytes_total"] == 100_000
    assert metadata["stdout_bytes_captured"] == MAX_STDOUT_BYTES
    head_bytes = int(MAX_STDOUT_BYTES * 0.4)
    assert text.startswith("H" * head_bytes)
    assert text.endswith("T" * (MAX_STDOUT_BYTES - head_bytes))
    assert "omitted" in text

    spill_path = metadata.get("stdout_spill_path")
    assert spill_path, metadata
    # Spill lives under the scratch dir this call was handed (never elsewhere).
    assert Path(spill_path).resolve().parent == (tmp_path / "spill").resolve()
    with open(spill_path, encoding="utf-8") as handle:
        spilled = handle.read()
    assert spilled == payload  # FULL text, not the truncated view
    assert "page it with the file/document tools" in metadata["warning"]


def test_failure_hint_rules():
    allowed = ["web_search", "url_fetch"]

    hint = output_module.failure_hint(
        "ImportError: cannot import name 'terminal' from 'gagent_tools' "
        "(/kernels/abc/gagent_tools.py)",
        allowed,
    )
    assert hint and "not available inside execute_code" in hint and "web_search" in hint

    hint = output_module.failure_hint(
        "ImportError: cannot import name 'json_parse' from 'gagent_tools' (x)", allowed
    )
    assert hint and "from gagent_tools import json_parse" in hint

    hint = output_module.failure_hint(
        "NameError: name 'retry' is not defined", allowed
    )
    assert hint and "from gagent_tools import retry" in hint

    hint = output_module.failure_hint(
        "ModuleNotFoundError: No module named 'pandas'", allowed
    )
    assert hint and "not installed in the kernel interpreter" in hint
    # The dependency hint must not present the delegation as the first answer
    # (upstream Hermes points at its own in-sandbox channel here): name what is
    # already importable, frame it as a dependency problem, and only then name
    # the escalation with its price.
    assert "Use the stack that is already there" in hint
    assert "dependency problem" in hint
    assert hint.index("Use the stack") < hint.index("code_executor")
    assert "pays a full coding agent" in hint

    hint = output_module.failure_hint(
        "TypeError: the JSON object must be str, bytes or bytearray, not dict",
        allowed,
    )
    assert hint and "already parsed" in hint and "json.loads" in hint

    assert output_module.failure_hint("SyntaxError: unexpected EOF", allowed) is None
    assert output_module.failure_hint("", allowed) is None


# --- integration: through a live kernel ----------------------------------------


@pytest.fixture(autouse=True)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.mark.timeout(60)
def test_oversized_stdout_truncates_and_spills_under_session_scratch(tmp_path):
    ctx = ToolContext(session_id="spill-test", work_dir=str(tmp_path))
    code = "print('x' * 200_000)"
    result = kernel_module.run_cell(
        code,
        session_id="spill-test",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert result["status"] == "success"
    assert result["stdout_truncated"] is True
    spill_path = result.get("stdout_spill_path")
    assert spill_path
    scratch = Path(resolve_scratch_dir(ctx.work_dir)).resolve()
    session_root = Path(ctx.work_dir).resolve()
    spilled = Path(spill_path).resolve()
    # Containment, not a "/tmp" string match: on Linux pytest's tmp_path *is*
    # /tmp/pytest-..., so "no /tmp in the path" only encoded the CI host's layout.
    # What the pipeline must guarantee is that the spill never escapes the
    # session: it has to be <work_dir>/scratch/code_mode/spill/<file>.
    assert spilled.parent == (scratch / "spill").resolve()
    assert scratch in spilled.parents
    assert session_root in spilled.parents
    assert "file/document tools" in result["warning"]


@pytest.mark.timeout(60)
def test_ansi_output_is_stripped_in_results(tmp_path):
    ctx = ToolContext(session_id="ansi-test", work_dir=str(tmp_path))
    result = kernel_module.run_cell(
        "print('\\x1b[32mgreen\\x1b[0m')",
        session_id="ansi-test",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert result["status"] == "success"
    assert result["output"].strip() == "green"


@pytest.mark.timeout(60)
def test_json_loads_dict_mistake_gets_actionable_hint(tmp_path):
    ctx = ToolContext(session_id="hint-test", work_dir=str(tmp_path))
    code = (
        "import json\n"
        "already_a_dict = {'a': 1}\n"
        "json.loads(already_a_dict)\n"
    )
    result = kernel_module.run_cell(
        code,
        session_id="hint-test",
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )
    assert result["status"] == "error"
    assert result.get("hint")
    assert "already parsed" in result["hint"]
