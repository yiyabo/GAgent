"""Oversized tool outputs are capped before they hit disk.

``store_tool_output`` used to write the full result and only then flag
``too_large_for_llm`` — a single huge tool result could land on disk at full
size. Oversized results are now stored as a small, still-parseable envelope
(P5, 2026-10-11).
"""

from __future__ import annotations

import json

from app.services import tool_output_storage as tos


def _read(base, relative: str):
    return json.loads((base / relative).read_text(encoding="utf-8"))


def test_oversized_tool_output_is_stored_as_truncated_envelope(isolated_app_env, monkeypatch) -> None:
    monkeypatch.setattr(tos, "MAX_OUTPUT_BYTES", 64)
    stored = tos.store_tool_output(
        session_id=None,
        job_id="job-1",
        action={"order": 1},
        tool_name="code_executor",
        raw_result={"stdout": "x" * 4096, "rows": list(range(200))},
        summary="big",
    )

    assert stored is not None
    assert stored.too_large_for_llm is True

    base = tos.get_runtime_root()
    payload = _read(base, stored.result_path)
    assert payload["truncated"] is True
    assert payload["original_bytes"] > 64

    manifest = _read(base, stored.manifest_path)
    assert manifest["result"]["too_large_for_llm"] is True
    assert manifest["result"]["truncated"] is True
    assert manifest["result"]["stored_bytes"] < manifest["result"]["size_bytes"]


def test_small_tool_output_round_trips_unchanged(isolated_app_env, monkeypatch) -> None:
    monkeypatch.setattr(tos, "MAX_OUTPUT_BYTES", 10 * 1024 * 1024)
    raw = {"stdout": "ok", "rows": [1, 2, 3]}
    stored = tos.store_tool_output(
        session_id=None,
        job_id="job-2",
        action={"order": 2},
        tool_name="code_executor",
        raw_result=raw,
        summary="small",
    )

    assert stored is not None
    assert stored.too_large_for_llm is False

    base = tos.get_runtime_root()
    payload = _read(base, stored.result_path)
    assert payload == raw
    manifest = _read(base, stored.manifest_path)
    assert manifest["result"]["truncated"] is False
