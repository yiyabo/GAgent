from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.deliverables.paper_builder import PaperBuilder
from tool_box.tools_impl import manuscript_writer as manuscript_writer_module
from tool_box.tools_impl import review_pack_writer as review_pack_writer_module


def _stub_chat_factory(
    *,
    intro_citation: str = "[@known1]",
    polish_pass: bool = True,
    final_polish_exc: Exception | None = None,
):
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
        if "final manuscript editor" in prompt:
            if final_polish_exc is not None:
                raise final_polish_exc
            return "## Final Manuscript\nPolished final manuscript."
        if "revising a manuscript after a final publication-readiness review" in prompt:
            if final_polish_exc is not None:
                raise final_polish_exc
            return "## Final Manuscript\nPolished final manuscript after revision."
        if "final release gate reviewer" in prompt:
            return json.dumps(
                {
                    "scores": {
                        "deduplication": 0.95 if polish_pass else 0.6,
                        "readability": 0.95 if polish_pass else 0.6,
                        "section_cohesion": 0.95 if polish_pass else 0.6,
                        "citation_integrity": 0.95 if polish_pass else 0.8,
                        "format_integrity": 0.95 if polish_pass else 0.7,
                        "factual_faithfulness": 0.95,
                    },
                    "defects": [] if polish_pass else ["duplication remains"],
                    "revision_instructions": [] if polish_pass else ["Remove repetition and tighten transitions."],
                    "release_summary": (
                        "Ready for publication-quality exposure."
                        if polish_pass
                        else "The manuscript still contains duplication and rough transitions."
                    ),
                    "pass": polish_pass,
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
    monkeypatch.setattr(
        manuscript_writer_module,
        "_build_llm_service",
        lambda provider, model, **kwargs: (object(), model),
    )
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
    assert result["polish_gate_passed"] is True
    assert result["public_release_ready"] is True
    assert result["release_state"] == "final"
    assert result["pre_polish_output_path"] is not None
    assert result["polished_output_path"] is not None

    analysis_file = tmp_path / "audit" / "analysis.md"
    assert analysis_file.exists()
    assert "Analysis Memo" in analysis_file.read_text(encoding="utf-8")


def test_manuscript_writer_blocks_release_when_final_polish_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        manuscript_writer_module,
        "_build_llm_service",
        lambda provider, model, **kwargs: (object(), model),
    )
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory(polish_pass=False))
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_ENABLED", "true")
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_MAX_REVISIONS", "2")
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_THRESHOLD", "0.85")

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
            output_path="runtime/session_demo/tool_outputs/review_pack_writer/review_draft.md",
            context_paths=["ctx/references.bib"],
            sections=["abstract", "introduction", "references"],
            keep_workspace=True,
            session_id="demo",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "polish_quality_gate_failed"
    assert result["polish_gate_passed"] is False
    assert result["public_release_ready"] is False
    assert result["release_state"] == "blocked"
    assert result["release_summary"]
    assert any(
        str(item).startswith(".manuscript_writer_") or str(item).startswith("tool_outputs/")
        for item in (result.get("hidden_artifact_prefixes") or [])
    )


def test_manuscript_writer_blocks_release_when_final_polish_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        manuscript_writer_module,
        "_build_llm_service",
        lambda provider, model, **kwargs: (object(), model),
    )
    monkeypatch.setattr(
        manuscript_writer_module,
        "_chat",
        _stub_chat_factory(final_polish_exc=asyncio.TimeoutError("The read operation timed out")),
    )
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_ENABLED", "true")
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_MAX_REVISIONS", "2")
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_THRESHOLD", "0.85")
    monkeypatch.setenv("MANUSCRIPT_FINAL_POLISH_STEP_TIMEOUT_SEC", "1")

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
            output_path="runtime/session_demo/tool_outputs/review_pack_writer/review_draft.md",
            context_paths=["ctx/references.bib"],
            sections=["abstract", "introduction", "references"],
            keep_workspace=True,
            session_id="demo",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "polish_quality_gate_failed"
    assert result["polish_gate_passed"] is False
    assert result["public_release_ready"] is False
    assert result["release_state"] == "blocked"
    assert "timed out" in str(result["release_summary"]).lower()
    release_review = result.get("release_review") or {}
    assert "polish_generation_timeout" in (release_review.get("defects") or [])
    assert any(
        str(item).startswith(".manuscript_writer_") or str(item).startswith("tool_outputs/")
        for item in (result.get("hidden_artifact_prefixes") or [])
    )


def test_final_polish_timeout_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"wait_for": False}

    async def _sample() -> str:
        return "ok"

    async def _fail_if_called(*args, **kwargs):
        called["wait_for"] = True
        raise AssertionError("asyncio.wait_for should not be used when timeout is disabled")

    monkeypatch.setattr(asyncio, "wait_for", _fail_if_called)

    result = asyncio.run(manuscript_writer_module._maybe_wait_with_timeout(_sample(), None))

    assert result == "ok"
    assert called["wait_for"] is False


def test_manuscript_writer_fails_on_missing_reference_coverage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        manuscript_writer_module,
        "_build_llm_service",
        lambda provider, model, **kwargs: (object(), model),
    )
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
    monkeypatch.setattr(
        manuscript_writer_module,
        "_build_llm_service",
        lambda provider, model, **kwargs: (object(), model),
    )
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
            "polish_gate_passed": False,
            "public_release_ready": False,
            "release_state": "blocked",
            "release_summary": "Publication blocked.",
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
    assert result["release_state"] == "blocked"
    assert result["public_release_ready"] is False


def test_literature_pipeline_scopes_default_output_by_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

    session_root = tmp_path / "runtime" / "session_demo" / "tool_outputs"

    def _fake_session_root(session_id: str, *, create: bool = False) -> Path:
        assert session_id == "demo"
        if create:
            session_root.mkdir(parents=True, exist_ok=True)
        return session_root

    async def _fake_esearch(_client, _query, retmax: int):
        assert retmax == 2
        return ["1"]

    async def _fake_efetch(_client, _pmids):
        return (
            "<PubmedArticleSet>"
            "<PubmedArticle>"
            "<MedlineCitation>"
            "<PMID>1</PMID>"
            "<Article>"
            "<ArticleTitle>Known phage study</ArticleTitle>"
            "<Abstract><AbstractText>Grounded abstract.</AbstractText></Abstract>"
            "<Journal><Title>Virology</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>"
            "<AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author></AuthorList>"
            "</Article>"
            "</MedlineCitation>"
            "<PubmedData><ArticleIdList><ArticleId IdType=\"doi\">10.1/example</ArticleId></ArticleIdList></PubmedData>"
            "</PubmedArticle>"
            "</PubmedArticleSet>"
        )

    monkeypatch.setattr(
        "app.services.session_paths.get_session_tool_outputs_dir",
        _fake_session_root,
    )
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_esearch", _fake_esearch)
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_efetch_xml", _fake_efetch)

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="phage host interaction",
            max_results=2,
            download_pdfs=False,
            session_id="demo",
        )
    )

    assert result["success"] is True
    output_dir = result["output_dir"]
    assert output_dir.startswith("runtime/session_demo/tool_outputs/literature_pipeline/review_pack_")
    assert (tmp_path / output_dir / "library.jsonl").exists()
    assert (tmp_path / output_dir / "references.bib").exists()
    assert (tmp_path / output_dir / "evidence.md").exists()


def test_review_pack_writer_forwards_session_id_and_uses_session_output_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    session_root = tmp_path / "runtime" / "session_demo" / "tool_outputs"

    def _fake_session_root(session_id: str, *, create: bool = False) -> Path:
        assert session_id == "demo"
        if create:
            session_root.mkdir(parents=True, exist_ok=True)
        return session_root

    captured: Dict[str, Dict[str, Any]] = {}

    async def _fake_lit(*args, **kwargs):
        _ = args
        captured["lit"] = dict(kwargs)
        return {
            "success": True,
            "outputs": {
                "evidence_md": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/evidence.md",
                "references_bib": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/references.bib",
            },
        }

    async def _fake_draft(*args, **kwargs):
        _ = args
        captured["draft"] = dict(kwargs)
        return {
            "success": True,
            "quality_gate_passed": True,
            "polish_gate_passed": True,
            "public_release_ready": True,
            "release_state": "final",
            "release_summary": "Ready for publication.",
            "output_path": kwargs["output_path"],
            "temp_workspace": "runtime/session_demo/tool_outputs/review_pack_writer/workspace",
            "hidden_artifact_prefixes": ["tool_outputs/review_pack_writer/review_pack_20260311_000000"],
        }

    monkeypatch.setattr(
        "app.services.session_paths.get_session_tool_outputs_dir",
        _fake_session_root,
    )
    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _fake_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _fake_draft)

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Test topic",
            session_id="demo",
        )
    )

    assert result["success"] is True
    assert result["public_release_ready"] is True
    assert result["release_state"] == "final"
    assert captured["lit"]["session_id"] == "demo"
    assert captured["draft"]["session_id"] == "demo"
    assert captured["lit"]["out_dir"].startswith(
        "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_"
    )
    assert captured["draft"]["output_path"].startswith(
        "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_"
    )
    assert result["hidden_artifact_prefixes"]


def test_manuscript_writer_review_mode_relaxes_experiment_and_result_rubrics() -> None:
    assert manuscript_writer_module._is_review_article_task(
        "Write a submission-ready English review article on Pseudomonas phage."
    )

    experiment_requirements = manuscript_writer_module._section_requirements(
        "experiment",
        review_mode=True,
    )
    result_requirements = manuscript_writer_module._section_requirements(
        "result",
        review_mode=True,
    )
    experiment_dims = manuscript_writer_module._section_eval_dims(
        "experiment",
        review_mode=True,
    )
    result_dims = manuscript_writer_module._section_eval_dims(
        "result",
        review_mode=True,
    )

    assert any("comparative synthesis" in item for item in experiment_requirements)
    assert any("original experimental data" in item for item in result_requirements)
    assert "results_analysis" not in experiment_dims
    assert "scientific_rigor" not in result_dims


def test_manuscript_writer_review_mode_prompts_flag_review_synthesis_context() -> None:
    requirements = manuscript_writer_module._section_requirements("result", review_mode=True)

    section_prompt = manuscript_writer_module._build_section_prompt(
        "Write a submission-ready English review article.",
        "result",
        "# Analysis Memo",
        "context",
        requirements,
        review_mode=True,
    )
    evaluation_prompt = manuscript_writer_module._build_evaluation_prompt(
        "result",
        "# Analysis Memo",
        "## Results\nSynthesis",
        requirements,
        review_mode=True,
    )
    revision_prompt = manuscript_writer_module._build_revision_prompt(
        "result",
        "# Analysis Memo",
        "context",
        "## Results\nSynthesis",
        {"scores": {"results_analysis": 0.7}, "defects": [], "revision_instructions": []},
        requirements,
        review_mode=True,
    )

    expected_marker = "review/synthesis article, not an original experimental report"
    assert expected_marker in section_prompt
    assert expected_marker in evaluation_prompt
    assert "Preserve explicit statements about missing quantitative evidence" in revision_prompt
