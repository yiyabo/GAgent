"""Targeted tests for the LaTeX -> PDF render path in artifact_routes (W0 gap).

`_render_latex_to_pdf` drives an external LaTeX toolchain
(xelatex/pdflatex -> bibtex -> two more passes) in a throwaway directory.
It had zero coverage; these tests mock the compiler executables and the
subprocess boundary (no real LaTeX on the host) and pin the invocation
contract plus the failure/timeout exits.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import app.routers.artifact_routes as artifact_routes


def _make_source(tmp_path: Path) -> Path:
    src_dir = tmp_path / "paper"
    (src_dir / "sections").mkdir(parents=True)
    (src_dir / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    (src_dir / "sections" / "intro.tex").write_text("\\section{Intro}\n", encoding="utf-8")
    return src_dir / "main.tex"


def _fake_which(mapping):
    def _which(cmd: str) -> Optional[str]:
        return mapping.get(cmd)

    return _which


def test_render_latex_returns_false_without_compiler(tmp_path, monkeypatch) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(shutil, "which", _fake_which({}))
    calls: List[list] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    assert artifact_routes._render_latex_to_pdf(source, tmp_path / "out.pdf") is False
    assert calls == []


def test_render_latex_compile_flow_invocation_contract(tmp_path, monkeypatch) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(
        shutil,
        "which",
        _fake_which({"xelatex": "/usr/bin/xelatex", "bibtex": "/usr/bin/bibtex"}),
    )
    calls: List[dict] = []

    def _fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None):
        calls.append({"argv": list(argv), "cwd": cwd, "timeout": timeout})
        work = Path(cwd)
        if argv[0] == "xelatex":
            # First pass leaves an .aux behind; every pass (re)writes the PDF.
            (work / "main.aux").write_text("\\citation{x}\n", encoding="utf-8")
            (work / "main.pdf").write_bytes(b"%PDF-1.4 fake\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    output = tmp_path / "cache" / "main.pdf"
    assert artifact_routes._render_latex_to_pdf(source, output) is True
    assert output.read_bytes() == b"%PDF-1.4 fake\n"

    latex_calls = [c for c in calls if c["argv"][0] == "xelatex"]
    # bibtex is invoked via its resolved absolute path from shutil.which.
    bibtex_calls = [c for c in calls if Path(c["argv"][0]).name == "bibtex"]
    # latex -> bibtex -> latex x2 (full bibliography flow).
    assert len(latex_calls) == 3
    assert len(bibtex_calls) == 1
    for call in latex_calls:
        assert call["argv"] == ["xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"]
        assert call["timeout"] == 120
        # Compiles inside the copied work dir, never against the source tree.
        assert Path(call["cwd"]).name == "paper"
        assert Path(call["cwd"]) != source.parent
    assert bibtex_calls[0]["argv"][1:] == ["main"]
    assert bibtex_calls[0]["timeout"] == 30


def test_render_latex_falls_back_to_pdflatex(tmp_path, monkeypatch) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(shutil, "which", _fake_which({"pdflatex": "/usr/bin/pdflatex"}))
    seen: List[str] = []

    def _fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None):
        seen.append(argv[0])
        (Path(cwd) / "main.pdf").write_bytes(b"%PDF-1.4 fake\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert artifact_routes._render_latex_to_pdf(source, tmp_path / "out.pdf") is True
    assert set(seen) == {"pdflatex"}


def test_render_latex_failure_without_pdf_returns_false(tmp_path, monkeypatch) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(shutil, "which", _fake_which({"xelatex": "/usr/bin/xelatex"}))

    def _fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None):
        return SimpleNamespace(returncode=1, stdout="", stderr="! Undefined control sequence.\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    output = tmp_path / "out.pdf"
    assert artifact_routes._render_latex_to_pdf(source, output) is False
    assert not output.exists()


def test_render_latex_timeout_returns_false(tmp_path, monkeypatch) -> None:
    source = _make_source(tmp_path)
    monkeypatch.setattr(shutil, "which", _fake_which({"xelatex": "/usr/bin/xelatex"}))

    def _fake_run(argv, cwd=None, capture_output=None, text=None, timeout=None):
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert artifact_routes._render_latex_to_pdf(source, tmp_path / "out.pdf") is False
