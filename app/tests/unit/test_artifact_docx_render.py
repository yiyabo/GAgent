from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.routers import artifact_routes


def test_docx_renderer_applies_content_security_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    document = tmp_path / "report.docx"
    document.write_bytes(b"placeholder")
    monkeypatch.setattr(artifact_routes, "MAMMOTH_AVAILABLE", True)
    monkeypatch.setattr(
        artifact_routes,
        "mammoth",
        SimpleNamespace(convert_to_html=lambda handle: SimpleNamespace(value="<h1>Report</h1>")),
        raising=False,
    )

    rendered = artifact_routes._render_docx_to_html(document)

    assert "Content-Security-Policy" in rendered
    assert "default-src 'none'" in rendered
    assert "<h1>Report</h1>" in rendered
