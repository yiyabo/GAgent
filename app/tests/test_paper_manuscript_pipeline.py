from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.deliverables.paper_builder import PaperBuilder
from tool_box.tools_impl import manuscript_writer as manuscript_writer_module
from tool_box.tools_impl import review_pack_writer as review_pack_writer_module


def _stub_chat_factory(*, intro_citation: str = "[@known1]"):
    async def _stub_chat(_llm, prompt: str, _model):
        if "produce an ANALYSIS MEMO" in prompt:
            return "# Analysis Memo\n- grounded context"
        if "Write the section:" in prompt:
            if "Introduction" in prompt:
                return f"## Introduction\nThis section cites {intro_citation}."
            if "Abstract" in prompt:
                return "## Abstract\nConcise summary."
            if "Methods" in prompt:
                return "## Methods\nMethod details."
            if "Experiments" in prompt:
                return "## Experiments\nExperiment setup."
            if "Results" in prompt:
                return "## Results\nResult interpretation."
            if "Conclusion" in prompt:
                return "## Conclusion\nConcluding remarks."
            if "References" in prompt:
                return "## References\nNot available"
            return "## Section\nDraft text."
        if "Evaluate the following section" in prompt:
            return json.dumps(
                {
                    "scores": {
                        "structure": 0.95,
                        "scientific_rigor": 0.92,
                    },
                    "defects": [],
                    "revision_instructions": [],
                    "pass": True,
                }
            )
        if "Perform a global rewrite" in prompt:
            return "## Final Manuscript\nMerged draft."
        raise AssertionError(f"Unexpected prompt: {prompt[:120]}")

    return _stub_chat


def test_paper_builder_infer_section_returns_none_for_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_SECTION_INFER_V2", "true")
    builder = PaperBuilder()

    assert builder.infer_section("Write methods section", None) == "method"
    assert builder.infer_section("Summarize experiment benchmark", None) == "experiment"
    assert builder.infer_section("Draft results and findings", None) == "result"
    assert builder.infer_section("Write conclusion and future work", None) == "conclusion"
    assert builder.infer_section("Refactor scheduler", None) is None


def test_paper_builder_uses_staged_figure_paths(tmp_path: Path) -> None:
    builder = PaperBuilder()
    paper_dir = tmp_path / "paper"
    refs_dir = tmp_path / "refs"
    builder.ensure_structure(paper_dir=paper_dir, refs_dir=refs_dir, title="Demo")

    main_tex = (paper_dir / "main.tex").read_text(encoding="utf-8")
    assert "\\graphicspath{{figures/}}" in main_tex

    section_path = builder.update_section(
        paper_dir=paper_dir,
        section="result",
        content="## Results\n![ROC curve](/tmp/generated/roc_curve.png)\n",
    )
    section_text = section_path.read_text(encoding="utf-8")
    assert "\\includegraphics[width=0.8\\textwidth]{roc_curve.png}" in section_text


def test_manuscript_writer_respects_analysis_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(manuscript_writer_module, "_build_llm_service", lambda provider, model: (object(), model))
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory())

    bib_path = tmp_path / "ctx" / "references.bib"
    bib_path.parent.mkdir(parents=True, exist_ok=True)
    bib_path.write_text(
        "@article{known1,\n"
        "  title={Known Paper},\n"
        "  author={Doe, Jane},\n"
        "  year={2025}\n"
        "}\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a manuscript.",
            output_path="out/final.md",
            analysis_path="audit/analysis.md",
            context_paths=["ctx/references.bib"],
            sections=["abstract", "introduction", "references"],
            keep_workspace=True,
        )
    )

    assert result["success"] is True
    assert result["analysis_path"] == "audit/analysis.md"
    assert result["effective_analysis_path"] == "audit/analysis.md"
    assert result["quality_gate_passed"] is True

    analysis_file = tmp_path / "audit" / "analysis.md"
    assert analysis_file.exists()
    assert "Analysis Memo" in analysis_file.read_text(encoding="utf-8")


def test_manuscript_writer_fails_on_missing_reference_coverage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(manuscript_writer_module, "_build_llm_service", lambda provider, model: (object(), model))
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory(intro_citation="[@known1]"))
    monkeypatch.setenv("MANUSCRIPT_STRICT_GATE", "true")

    bib_path = tmp_path / "ctx" / "references.bib"
    bib_path.parent.mkdir(parents=True, exist_ok=True)
    bib_path.write_text(
        "@article{known1,\n"
        "  title={Known Paper},\n"
        "  author={Doe, Jane},\n"
        "  year={2025}\n"
        "}\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a manuscript.",
            output_path="out/final.md",
            context_paths=["ctx/references.bib"],
            sections=["introduction"],
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error"] == "citation_validation_failed"
    assert result["quality_gate_passed"] is False
    citation = result.get("citation_validation") or {}
    assert citation.get("unknown_citekeys") == []
    assert citation.get("missing_reference_citekeys") == ["known1"]


def test_manuscript_writer_fails_on_unknown_citekey(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(manuscript_writer_module, "_build_llm_service", lambda provider, model: (object(), model))
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory(intro_citation="[@unknown_key]"))
    monkeypatch.setenv("MANUSCRIPT_STRICT_GATE", "true")

    context_path = tmp_path / "ctx" / "notes.md"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text("No bibliography file is provided.", encoding="utf-8")

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a manuscript.",
            output_path="out/final.md",
            context_paths=["ctx/notes.md"],
            sections=["introduction", "references"],
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error"] == "citation_validation_failed"
    citation = result.get("citation_validation") or {}
    assert citation.get("unknown_citekeys") == ["unknown_key"]


def test_review_pack_partial_always_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    async def _fake_lit(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "success": True,
            "outputs": {
                "evidence_md": "runtime/literature/evidence.md",
                "references_bib": "runtime/literature/references.bib",
            },
        }

    async def _fake_draft(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "success": False,
            "quality_gate_passed": False,
            "error": "section_evaluation_failed",
            "partial_output_path": "runtime/literature/review_draft.partial.md",
        }

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _fake_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _fake_draft)
    monkeypatch.setenv("MANUSCRIPT_STRICT_GATE", "true")

    result = asyncio.run(review_pack_writer_module.review_pack_writer_handler(topic="Test topic"))

    assert result["success"] is False
    assert result["partial"] is True
    assert result["error_code"] == "section_evaluation_failed"
    assert result["partial_output_path"] == "runtime/literature/review_draft.partial.md"
