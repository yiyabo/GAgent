"""``vision_reader``'s PDF path: one paid reader, no phantom local renderer.

Until 2026-09-26 ``read_pdf`` fell back to a local pdf2image+poppler renderer
whenever the file-extract reader failed. That renderer could not run in
production at all — ``pdf2image`` and ``pytesseract`` are in no dependency
manifest, and the host has no poppler — so every fallback ended in the string
"Missing pdf2image". Deleting it is therefore behaviour-preserving: the tool
already failed there, and now it fails with the *real* reader's error instead.

The absence guards below are the point: the render path can only come back as a
deliberate decision with a pinned dependency, never as a copied helper.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tool_box.tools_impl import vision_reader

_SANDBOX = Path("runtime/test_vision_reader_pdf_path_sandbox")


@pytest.fixture()
def sandbox() -> Path:
    if _SANDBOX.exists():
        shutil.rmtree(_SANDBOX)
    _SANDBOX.mkdir(parents=True)
    yield _SANDBOX
    shutil.rmtree(_SANDBOX, ignore_errors=True)


def _pdf(sandbox: Path, name: str = "paper.pdf") -> str:
    path = sandbox / name
    path.write_bytes(b"%PDF-1.4\n% fixture placeholder\n")
    return str(path)


async def test_file_extract_failure_is_returned_directly(sandbox: Path, monkeypatch) -> None:
    """A failed file-extract is the answer; nothing local is attempted after it."""
    async def _fail(_path: str, prompt: str = "") -> dict:
        return {"success": False, "error": "upstream 500", "method": "qwen-long"}

    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _fail)

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=_pdf(sandbox)
    )

    assert result["success"] is False
    assert result["error"] == "upstream 500"
    assert result["tool"] == "vision_reader"
    assert result["operation"] == "read_pdf"
    assert "fallback_from" not in result


def test_local_render_path_is_absent() -> None:
    """The dead renderer and its pdf2image dependency are gone from the module."""
    assert not hasattr(vision_reader, "_convert_pdf_to_images")
    assert not hasattr(vision_reader, "_read_pdf_with_vision")

    source = Path(vision_reader.__file__).read_text(encoding="utf-8")
    assert "pdf2image" not in source
    assert "pytesseract" not in source


async def test_paid_path_names_the_missing_sdk(sandbox: Path, monkeypatch) -> None:
    """A blocked paid path must say *why* it is blocked.

    ``openai`` is declared in no requirements file, so a shipped image has no
    file-extract reader. The failure names the dependency instead of blaming the
    document ("openai SDK not installed, falling back to vision model" was both
    wrong — there is no vision fallback left — and unactionable).
    """
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)

    result = await vision_reader._read_pdf_with_qwen_long(_pdf(sandbox))

    assert result["success"] is False
    assert result["code"] == "pdf_extract_sdk_missing"
    assert "openai" in result["error"]


async def test_pdf_path_does_not_import_the_renderer_stack(sandbox: Path, monkeypatch) -> None:
    """Reading a PDF must not pull an undeclared dependency in, even by accident."""
    import sys

    async def _ok(_path: str, prompt: str = "") -> dict:
        return {"success": True, "method": "qwen-long", "text": "hello", "text_length": 5}

    monkeypatch.setattr(vision_reader, "_read_pdf_with_qwen_long", _ok)
    for name in ("pdf2image", "pytesseract"):
        sys.modules.pop(name, None)

    result = await vision_reader.vision_reader_handler(
        operation="read_pdf", file_path=_pdf(sandbox)
    )

    assert result["success"] is True
    assert "pdf2image" not in sys.modules
    assert "pytesseract" not in sys.modules
