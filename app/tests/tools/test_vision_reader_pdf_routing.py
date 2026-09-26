"""``vision_reader``'s PDF routing: free text first, paid pages second.

Two decisions are locked here, both cost decisions:

- A text PDF is read locally with pypdf — no upload, no per-page charge. Only a
  document that yields no text (a scan or an image PDF) is worth paying for.
- The paid reader is bounded. A page is a charge, so a page selection decides the
  bill, and a whole-document parse above the budget is refused with the way out
  instead of silently costing 50x what the caller meant.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List

import pypdf
import pytest

from tool_box.tools_impl import vision_reader

_SANDBOX = Path("runtime/test_vision_reader_pdf_routing_sandbox")


@pytest.fixture()
def sandbox() -> Path:
    if _SANDBOX.exists():
        shutil.rmtree(_SANDBOX)
    _SANDBOX.mkdir(parents=True)
    yield _SANDBOX
    shutil.rmtree(_SANDBOX, ignore_errors=True)


def _write_pdf(path: Path, page_texts: List[str]) -> Path:
    """Hand-build a real PDF: pypdf reads it, and an empty page has no text."""
    objects: List[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    pages_ref = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: List[int] = []
    for text in page_texts:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        content = add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_ids.append(
            add(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
                + str(content).encode()
                + b" 0 R /Resources << /Font << /F1 "
                + str(font).encode()
                + b" 0 R >> >> >>"
            )
        )

    objects[catalog - 1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[pages_ref - 1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{page_id} 0 R".encode() for page_id in page_ids)
        + b"] /Count "
        + str(len(page_ids)).encode()
        + b" >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_position}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


def _text_page(label: str = "lorem ipsum dolor sit amet") -> str:
    return f"{label} " * 120


def _read_pages(path: Path) -> List[str]:
    with path.open("rb") as handle:
        reader = pypdf.PdfReader(handle)
        return [page.extract_text() or "" for page in reader.pages]


async def test_text_pdf_is_read_locally_and_never_pays(sandbox: Path, monkeypatch) -> None:
    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("a text PDF must not be uploaded to the paid reader")

    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _must_not_run)
    pdf = _write_pdf(sandbox / "text.pdf", [_text_page()])

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=str(pdf)
    )

    assert result["success"] is True
    assert result["method"] == "pypdf-local"
    assert result["page_count"] == 1
    assert "lorem ipsum" in result["text"]


async def test_image_only_pdf_falls_through_to_the_paid_reader(
    sandbox: Path, monkeypatch
) -> None:
    calls: List[str] = []

    async def _paid(path: str, prompt: str = "") -> dict:
        calls.append(path)
        return {"success": True, "method": "qwen-long", "text": "ocr", "text_length": 3}

    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _paid)
    pdf = _write_pdf(sandbox / "scan.pdf", ["", ""])

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=str(pdf)
    )

    assert calls == [str(pdf.resolve())]
    assert result["method"] == "qwen-long"
    assert result["source_page_count"] == 2


async def test_page_selection_uploads_only_those_pages(sandbox: Path, monkeypatch) -> None:
    uploaded: Dict[str, object] = {}

    async def _paid(path: str, prompt: str = "") -> dict:
        texts = _read_pages(Path(path))
        uploaded["path"] = Path(path)
        uploaded["texts"] = texts
        return {
            "success": True,
            "method": "qwen-long",
            "page_count": len(texts),
            "text": "selected",
            "text_length": 8,
        }

    # Local-first off so the selection reaches the paid reader; the pages
    # themselves are what this test inspects.
    monkeypatch.setenv(vision_reader.LOCAL_TEXT_FIRST_ENV, "0")
    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _paid)
    pdf = _write_pdf(sandbox / "long.pdf", ["", _text_page("two"), _text_page("three"), ""])

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=str(pdf), page_numbers=[3, 2]
    )

    assert uploaded["path"] != pdf
    texts = uploaded["texts"]
    assert len(texts) == 2
    # Exactly the requested pages, in one canonical order.
    assert "two" in texts[0]
    assert "three" in texts[1]
    assert result["page_count"] == 2
    assert result["pages_parsed"] == [2, 3]
    assert result["source_page_count"] == 4
    # The caller is told which document was read, not the scratch subset.
    assert result["file_path"] == str(pdf.resolve())


async def test_whole_document_above_budget_is_refused(sandbox: Path, monkeypatch) -> None:
    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("an over-budget document must not be parsed")

    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _must_not_run)
    pdf = _write_pdf(sandbox / "thesis.pdf", ["", "", "", ""])

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=str(pdf), max_pages=3
    )

    assert result["success"] is False
    assert result["code"] == "page_budget_exceeded"
    assert result["page_count"] == 4
    assert result["page_budget"] == 3
    assert "page_numbers" in result["error"]


async def test_requested_pages_above_budget_are_refused(sandbox: Path, monkeypatch) -> None:
    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("an over-budget selection must not be parsed")

    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _must_not_run)
    pdf = _write_pdf(sandbox / "thesis.pdf", ["", "", "", ""])

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf",
        file_path=str(pdf),
        page_numbers=[1, 2, 3],
        max_pages=2,
    )

    assert result["success"] is False
    assert result["code"] == "page_budget_exceeded"


async def test_local_text_first_can_be_disabled(sandbox: Path, monkeypatch) -> None:
    calls: List[str] = []

    async def _paid(path: str, prompt: str = "") -> dict:
        calls.append(path)
        return {"success": True, "method": "qwen-long", "text": "paid", "text_length": 4}

    monkeypatch.setenv(vision_reader.LOCAL_TEXT_FIRST_ENV, "0")
    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _paid)
    pdf = _write_pdf(sandbox / "text.pdf", [_text_page()])

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=str(pdf)
    )

    assert calls == [str(pdf.resolve())]
    assert result["method"] == "qwen-long"


def test_local_extraction_returns_none_without_text(sandbox: Path) -> None:
    assert vision_reader.extract_local_pdf_text(_write_pdf(sandbox / "scan.pdf", ["", ""])) is None


def test_local_extraction_reads_only_the_requested_pages(sandbox: Path) -> None:
    pdf = _write_pdf(
        sandbox / "mixed.pdf",
        [_text_page("one"), _text_page("two"), _text_page("three")],
    )

    extracted = vision_reader.extract_local_pdf_text(pdf, page_numbers=[3])

    assert extracted is not None
    assert extracted["pages_read"] == [3]
    assert extracted["page_count"] == 3
    assert "three" in extracted["text"]
    assert "two" not in extracted["text"]


def test_page_budget_override_beats_the_env(sandbox: Path, monkeypatch) -> None:
    monkeypatch.setenv(vision_reader.PDF_MAX_PAGES_ENV, "7")

    assert vision_reader.pdf_page_budget() == 7
    assert vision_reader.pdf_page_budget(2) == 2
    assert vision_reader.pdf_page_budget(0) == 7
