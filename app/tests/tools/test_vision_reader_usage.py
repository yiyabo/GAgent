"""``vision_reader`` bills the project credential; its usage must be visible.

Both paid paths — the PDF file-extract reader (flat per-page rate) and the
multimodal vision call (tokens) — recorded nothing before 2026-09-26, so a few
hundred pages of PDF parsing was an unbounded, invisible charge. The rows below
are the whole point of the metering step: page count, tokens, tool key and the
ambient session/run attribution, so cost is attributed per conversation turn
instead of vanishing into the provider bill.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm import _usage_context as _llm_usage_ctx
from app.repository.llm_usage import init_llm_usage_table
from tool_box.tools_impl import vision_reader


@pytest.fixture(autouse=True)
def _reset_usage_context():
    _llm_usage_ctx.set(None)
    yield
    _llm_usage_ctx.set(None)


@pytest.fixture
def ledger_db(tmp_path: Path):
    """Point the shared SQLite pool at a throwaway ledger instead of the repo DB."""
    from app.database_pool import close_connection_pool, initialize_connection_pool

    db_path = tmp_path / "llm_usage_ledger.db"
    initialize_connection_pool(db_path=str(db_path))
    try:
        init_llm_usage_table()
        yield db_path
    finally:
        close_connection_pool()


def _rows(db_path: Path) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM llm_usage_log")]


def test_page_billed_pdf_parse_records_pages_tokens_and_charge(ledger_db: Path) -> None:
    from app.llm import set_usage_context

    set_usage_context(session_id="sess_pdf", plan_id=7, task_id=3, run_id="run_pdf")
    vision_reader.record_usage(
        provider="qwen",
        model="qwen-long",
        prompt_tokens=1200,
        completion_tokens=30,
        call_purpose=vision_reader.CALL_PURPOSE_PDF_PARSE,
        page_count=300,
    )

    rows = _rows(ledger_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["tool_name"] == "vision_reader"
    assert row["call_purpose"] == "pdf_parse"
    assert row["billing_key"] == "tool.vision_reader"
    assert row["session_id"] == "sess_pdf"
    assert row["plan_id"] == 7
    assert row["task_id"] == 3
    assert row["run_id"] == "run_pdf"
    assert row["page_count"] == 300
    assert row["prompt_tokens"] == 1200
    assert row["completion_tokens"] == 30
    # No configured token rate for qwen-long, so the page charge is the whole cost.
    assert row["estimated_cost"] == pytest.approx(300 * vision_reader.DEFAULT_PDF_PAGE_CNY)
    # A tool call inside a run is not a delegated run of its own.
    assert row["parent_run_id"] is None


def test_failed_parse_still_records_the_page_charge(ledger_db: Path) -> None:
    vision_reader.record_usage(
        provider="qwen",
        model="qwen-long",
        prompt_tokens=0,
        completion_tokens=0,
        call_purpose=vision_reader.CALL_PURPOSE_PDF_PARSE,
        call_status="error",
        page_count=12,
    )

    row = _rows(ledger_db)[0]
    assert row["call_status"] == "error"
    assert row["page_count"] == 12
    assert row["estimated_cost"] == pytest.approx(12 * vision_reader.DEFAULT_PDF_PAGE_CNY)


def test_image_read_records_tokens_without_pages(ledger_db: Path) -> None:
    vision_reader.record_usage(
        provider="qwen",
        model="qwen3.6-plus",
        prompt_tokens=800,
        completion_tokens=120,
        call_purpose=vision_reader.CALL_PURPOSE_VISION_READ,
    )

    row = _rows(ledger_db)[0]
    assert row["call_purpose"] == "vision_read"
    assert row["page_count"] is None
    assert row["total_tokens"] == 920


async def test_read_pdf_with_qwen_long_records_through_the_api_path(
    ledger_db: Path, monkeypatch, tmp_path: Path
) -> None:
    import sys

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% placeholder\n")

    class _Files:
        def create(self, file, purpose):  # noqa: A002 - mirrors the SDK signature
            assert purpose == "file-extract"
            return SimpleNamespace(id="file-abc")

    class _Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="full text"))],
                usage=SimpleNamespace(prompt_tokens=4500, completion_tokens=60),
            )

    class _Client:
        def __init__(self, **kwargs):
            self.files = _Files()
            self.chat = SimpleNamespace(completions=_Completions())

    # The reader imports the SDK lazily; a stub module keeps the API path
    # testable where openai is not installed.
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_Client))
    monkeypatch.setattr(vision_reader, "is_production", lambda: False)
    monkeypatch.setattr(vision_reader, "count_pdf_pages", lambda path: 9)
    monkeypatch.setenv("QWEN_API_KEY", "test-key")

    result = await vision_reader._read_pdf_with_qwen_long(str(pdf))

    assert result["success"] is True
    assert result["page_count"] == 9
    row = _rows(ledger_db)[0]
    assert row["call_purpose"] == "pdf_parse"
    assert row["page_count"] == 9
    assert row["prompt_tokens"] == 4500
    assert row["completion_tokens"] == 60
    assert row["estimated_cost"] == pytest.approx(9 * vision_reader.DEFAULT_PDF_PAGE_CNY)


def test_count_pdf_pages_is_optional_not_fatal(tmp_path: Path) -> None:
    """A page count is billing metadata: an unreadable file must not fail the read."""
    bogus = tmp_path / "not-really.pdf"
    bogus.write_bytes(b"this is not a pdf")

    assert vision_reader.count_pdf_pages(bogus) is None
