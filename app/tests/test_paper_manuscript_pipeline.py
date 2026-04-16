from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services.deliverables.paper_builder import PaperBuilder
from tool_box.tools_impl import manuscript_writer as manuscript_writer_module
from tool_box.tools_impl import review_pack_writer as review_pack_writer_module


def _write_review_evidence_bundle(tmp_path: Path, *, low_coverage: bool = False) -> list[str]:
    ctx_dir = tmp_path / "ctx"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    study_cards_path = ctx_dir / "study_cards.jsonl"
    coverage_report_path = ctx_dir / "coverage_report.json"
    evidence_md_path = ctx_dir / "evidence.md"
    references_bib_path = ctx_dir / "references.bib"

    cards = []
    total = 6 if low_coverage else 16
    quantitative_cutoff = 1 if low_coverage else 6
    for idx in range(1, total + 1):
        cards.append(
            {
                "citekey": f"known{idx}",
                "title": f"Study {idx}",
                "authors": ["Doe, Jane"],
                "year": "2026",
                "journal": "BioAI",
                "evidence_tier": "full_text" if idx <= (2 if low_coverage else 8) else "abstract_only",
                "study_type": "animal_model" if idx % 2 == 0 else "in_vitro",
                "model_system": ["murine_model"] if idx % 2 == 0 else ["biofilm_assay"],
                "intervention_delivery": ["phage_cocktail"] if idx % 3 == 0 else ["hydrogel_delivery"],
                "receptor_mechanism_terms": ["LptD", "Psl"] if idx % 2 == 0 else ["LPS"],
                "quantitative_findings": [f"{idx} log CFU reduction after therapy."]
                if idx <= quantitative_cutoff
                else [],
                "limitations": ["Heterogeneity across infection models."],
                "supporting_snippets": [f"Study {idx} reports phage activity in Pseudomonas infection."],
                "section_support": [
                    "introduction",
                    "method",
                    "experiment",
                    "result",
                    "discussion",
                    "conclusion",
                ],
            }
        )

    study_cards_path.write_text(
        "\n".join(json.dumps(card, ensure_ascii=False) for card in cards) + "\n",
        encoding="utf-8",
    )

    coverage_report = {
        "profile": "pi_ready_review",
        "pass": not low_coverage,
        "summary": (
            "Evidence coverage blocked: only 6 included studies; only 2 full-text studies; only 1 study with quantitative findings."
            if low_coverage
            else "Evidence coverage passed: 16 studies included, 8 full-text studies, 6 studies with quantitative findings, and all core review sections are supported."
        ),
        "thresholds": {
            "min_total_studies": 15,
            "min_full_text_studies": 6,
            "min_quantitative_studies": 4,
            "min_support_per_core_section": 2,
        },
        "counts": {
            "total_studies": total,
            "full_text_studies": 2 if low_coverage else 8,
            "quantitative_studies": 1 if low_coverage else 6,
        },
        "section_support_counts": {
            "introduction": total,
            "method": total,
            "experiment": total,
            "result": total,
            "discussion": total,
            "conclusion": total,
        },
        "failures": (
            [
                "only 6 included studies; require at least 15",
                "only 2 full-text studies; require at least 6",
                "only 1 study with quantitative findings; require at least 4",
            ]
            if low_coverage
            else []
        ),
    }
    coverage_report_path.write_text(json.dumps(coverage_report, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence_md_path.write_text(
        "# Literature evidence inventory\n\n## Coverage summary\nEvidence coverage summary.\n",
        encoding="utf-8",
    )
    bib_chunks: list[str] = []
    for idx in range(1, total + 1):
        bib_chunks.append(
            "\n".join(
                [
                    f"@article{{known{idx},",
                    f"  title={{Study {idx}}},",
                    "  author={Doe, Jane and Smith, Alex},",
                    "  journal={BioAI},",
                    "  year={2026},",
                    f"  doi={{10.1/example{idx}}}",
                    "}",
                ]
            )
        )
    references_bib_path.write_text(
        "\n\n".join(bib_chunks) + "\n",
        encoding="utf-8",
    )

    return [
        str(study_cards_path.relative_to(tmp_path)),
        str(coverage_report_path.relative_to(tmp_path)),
        str(evidence_md_path.relative_to(tmp_path)),
        str(references_bib_path.relative_to(tmp_path)),
    ]


def _stub_chat_factory(
    *,
    intro_citation: str = "[@known1]",
    review_mode: bool = False,
    review_intro_citations: str = "[@known1; @known2]",
    review_method_citations: str = "[@known3; @known4]",
    review_experiment_citations: str = "[@known1; @known2; @known3]",
    review_result_citations: str = "[@known4; @known5; @known6]",
    review_discussion_citations: str = "[@known5; @known6; @known7]",
    review_conclusion_citations: str = "[@known8; @known9]",
    polish_pass: bool = True,
    final_polish_exc: Exception | None = None,
    final_polish_text: str | None = None,
    final_polish_revision_text: str | None = None,
):
    def _extract_prompt_body(prompt: str, marker: str) -> str | None:
        if marker not in prompt:
            return None
        tail = prompt.split(marker, 1)[1]
        tail = tail.rsplit("\n\nReturn ONLY", 1)[0]
        body = tail.strip()
        return body or None

    def _extract_section_name(prompt: str) -> str:
        first_line = str(prompt or "").splitlines()[0].strip().lower()
        for prefix in ("write the section:", "revise the section:"):
            if first_line.startswith(prefix):
                return first_line.split(":", 1)[1].strip()
        return ""

    async def _stub_chat(_llm, prompt: str, _model):
        if "produce an ANALYSIS MEMO" in prompt:
            return "# Analysis Memo\n- grounded context"
        if "Write the section:" in prompt or "Revise the section:" in prompt:
            section_name = _extract_section_name(prompt)
            if section_name == "introduction":
                if review_mode:
                    return f"## Introduction\nThis review frames the therapeutic challenge and cites {review_intro_citations}."
                return f"## Introduction\nThis section cites {intro_citation}."
            if section_name == "abstract":
                if review_mode:
                    return (
                        "## Abstract\n"
                        "Pseudomonas aeruginosa remains a major antimicrobial-resistance challenge. "
                        "This review synthesizes recent literature on therapeutic phages. "
                        "The evidence base includes full-text and abstract-level studies identified from the literature search. "
                        "Key findings show recent studies support receptor-aware cocktails and delivery optimization. "
                        "However, the evidence remains limited by heterogeneity and incomplete quantitative reporting. "
                        "Overall, the review suggests phage therapy remains promising with careful evidence-linked deployment."
                    )
                return "## Abstract\nConcise summary."
            if section_name == "methods":
                if review_mode:
                    return f"## Methods\nThis review summarizes the evidence workflow and cites {review_method_citations}."
                return "## Methods\nMethod details."
            if section_name == "experiments":
                if review_mode:
                    return f"## Experiments\nAcross representative models, cited studies {review_experiment_citations} compare assays, controls, and endpoints."
                return "## Experiments\nExperiment setup."
            if section_name == "results":
                if review_mode:
                    return f"## Results\nAcross studies {review_result_citations}, comparative synthesis highlights receptor usage and quantitative reductions in bacterial burden."
                return "## Results\nResult interpretation."
            if section_name == "discussion":
                if review_mode:
                    return f"## Discussion\nThese studies {review_discussion_citations} suggest translational promise, but heterogeneity remains a limitation."
                return "## Discussion\nDiscussion points."
            if section_name == "conclusion":
                if review_mode:
                    return f"## Conclusion\nTaken together, the evidence {review_conclusion_citations} supports cautious optimism for phage therapy."
                return "## Conclusion\nConcluding remarks."
            if section_name == "references":
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
            if final_polish_text is not None:
                return final_polish_text
            extracted = _extract_prompt_body(prompt, "Manuscript draft:\n")
            if extracted is not None:
                return extracted
            return "## Final Manuscript\nPolished final manuscript."
        if "revising a manuscript after a final publication-readiness review" in prompt:
            if final_polish_exc is not None:
                raise final_polish_exc
            if final_polish_revision_text is not None:
                return final_polish_revision_text
            if final_polish_text is not None:
                return final_polish_text
            extracted = _extract_prompt_body(prompt, "Current polished draft:\n")
            if extracted is not None:
                return extracted
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
    assert "\\graphicspath{{../image_tabular/}}" in main_tex

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


def test_manuscript_writer_scopes_relative_output_path_to_session_dir(
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
            task="Write the preprocessing methods section.",
            output_path="manuscript/methods/data_preprocessing.md",
            context_paths=["ctx/references.bib"],
            sections=["method", "references"],
            session_id="session_demo",
            keep_workspace=True,
        )
    )

    assert result["success"] is True
    assert result["output_path"] == "runtime/session_demo/manuscript/methods/data_preprocessing.md"

    output_file = tmp_path / "runtime" / "session_demo" / "manuscript" / "methods" / "data_preprocessing.md"
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8").strip()


def test_manuscript_writer_draft_only_mode_skips_quality_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(manuscript_writer_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manuscript_writer_module, "_RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        manuscript_writer_module,
        "_build_llm_service",
        lambda provider, model, **kwargs: (_ for _ in ()).throw(AssertionError("_build_llm_service should not be called in draft_only mode")),
    )

    async def _unexpected_chat(_llm, _prompt: str, _model):
        raise AssertionError("_chat should not be called in draft_only mode")

    monkeypatch.setattr(manuscript_writer_module, "_chat", _unexpected_chat)

    ctx_path = tmp_path / "manuscript" / "results" / "5.1.3.1_atlas_composition.md"
    ctx_path.parent.mkdir(parents=True, exist_ok=True)
    ctx_path.write_text("## Results\nIntegrated findings.", encoding="utf-8")

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Assemble a local manuscript draft.",
            output_path="runtime/session_demo/manuscript/manuscript_draft.md",
            context_paths=["manuscript/results/5.1.3.1_atlas_composition.md"],
            sections=["abstract", "result", "references"],
            draft_only=True,
        )
    )

    assert result["success"] is True
    assert result["draft_only"] is True
    assert result["release_state"] == "draft"
    assert result["public_release_ready"] is False
    assert result["output_path"] == "runtime/session_demo/manuscript/manuscript_draft.md"
    assert result["analysis_path"] == "runtime/session_demo/manuscript/manuscript_draft.md.analysis.md"
    assert result["section_profile"] == "bio_manuscript"
    assert result["applicable_sections"] == ["abstract", "result"]
    assert result["completed_sections"] == ["result"]
    assert result["missing_sections"] == ["abstract"]
    assert len(result["sections"]) == 1
    assert result["run_stats"]["draft_only"] is True
    assert result["run_stats"]["source_file_count"] == 1

    output_file = tmp_path / "runtime" / "session_demo" / "manuscript" / "manuscript_draft.md"
    section_file = tmp_path / "runtime" / "session_demo" / "manuscript" / ".manuscript_draft_sections" / "02_result.md"
    assert output_file.exists()
    assert section_file.exists()
    assert "Integrated findings" in output_file.read_text(encoding="utf-8")
    assert "Integrated findings" in section_file.read_text(encoding="utf-8")


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


def test_manuscript_writer_polish_guard_blocks_citation_and_numeric_drift(
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

    polished_text = (
        "## Abstract\nConcise summary with 42 patients.\n\n"
        "## Introduction\nThis section cites [@known1; @newref].\n\n"
        "## References\n[@known1]\n[@newref]\n"
    )
    monkeypatch.setattr(
        manuscript_writer_module,
        "_chat",
        _stub_chat_factory(final_polish_text=polished_text),
    )

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
            sections=["abstract", "introduction", "references"],
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "polish_quality_gate_failed"
    consistency = result.get("release_consistency_report") or {}
    defects = consistency.get("defects") or []
    assert "citation_set_changed" in defects
    assert "numeric_claims_changed" in defects
    release_review = result.get("release_review") or {}
    assert "citation_set_changed" in (release_review.get("defects") or [])


def test_final_polish_timeout_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"wait_for": False}

    async def _sample() -> str:
        return "ok"

    async def _fail_if_called(*_args, **_kwargs):
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


def test_manuscript_writer_blocks_review_release_without_structured_evidence(
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
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory(review_mode=True))

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a submission-ready English review article on Pseudomonas phage.",
            output_path="out/review.md",
            context_paths=[],
            sections=["abstract", "introduction", "method", "experiment", "result", "discussion", "conclusion", "references"],
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "low_evidence_coverage"
    assert result["public_release_ready"] is False
    assert result["evidence_coverage_passed"] is False
    assert "study_cards" in str(result["coverage_summary"]).lower()


def test_manuscript_writer_article_mode_review_overrides_task_heuristic(
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
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory(review_mode=True))

    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a manuscript on Pseudomonas phage.",
            output_path="out/review.md",
            context_paths=[],
            article_mode="review",
            sections=["abstract", "introduction", "method", "experiment", "result", "discussion", "conclusion", "references"],
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "low_evidence_coverage"
    assert result["article_mode_requested"] == "review"
    assert result["article_mode_resolved"] == "review"


def test_manuscript_writer_review_mode_passes_with_structured_evidence(
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
    monkeypatch.setattr(manuscript_writer_module, "_chat", _stub_chat_factory(review_mode=True))

    context_paths = _write_review_evidence_bundle(tmp_path)
    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a submission-ready English review article on Pseudomonas phage.",
            output_path="out/review.md",
            context_paths=context_paths,
            keep_workspace=True,
        )
    )

    assert result["success"] is True
    assert result["public_release_ready"] is True
    assert result["evidence_coverage_passed"] is True
    assert result["coverage_report_path"]
    assert result["evidence_coverage_path"]
    assert result["study_matrix_path"]
    assert result["reference_library_path"].endswith("references.bib")


def test_manuscript_writer_review_mode_blocks_unsupported_claims(
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
        _stub_chat_factory(
            review_mode=True,
            review_intro_citations="[@known1]",
            review_method_citations="[@known2]",
            review_experiment_citations="[@known3]",
            review_result_citations="[@known4]",
            review_discussion_citations="[@known5]",
            review_conclusion_citations="[@known6]",
        ),
    )

    context_paths = _write_review_evidence_bundle(tmp_path)
    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a submission-ready English review article on Pseudomonas phage.",
            output_path="out/review.md",
            context_paths=context_paths,
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "unsupported_claims"
    assert result["public_release_ready"] is False
    assert "insufficient_evidence_linkage" in str(result["sections"])


def test_manuscript_writer_review_mode_blocks_thin_section_coverage(
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
        _stub_chat_factory(
            review_mode=True,
            review_experiment_citations="[@known1; @known2]",
            review_result_citations="[@known3; @known4]",
            review_discussion_citations="[@known5; @known6]",
            review_conclusion_citations="[@known7; @known8]",
        ),
    )

    context_paths = _write_review_evidence_bundle(tmp_path)
    result = asyncio.run(
        manuscript_writer_module.manuscript_writer_handler(
            task="Write a submission-ready English review article on Pseudomonas phage.",
            output_path="out/review.md",
            context_paths=context_paths,
            keep_workspace=True,
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "unsupported_claims"
    assert "insufficient_review_evidence_coverage" in str(result["sections"])

    result_row = next(row for row in result["sections"] if row["section"] == "result")
    coverage = result_row.get("review_evidence_coverage") or {}
    assert coverage.get("target_supported_citations") == 3
    assert coverage.get("cited_supported_studies") == 2
    assert coverage.get("pass") is False

    evaluation_path = tmp_path / str(result_row["evaluation_path"])
    evaluation_payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
    assert evaluation_payload["review_evidence_coverage"]["score"] < 0.85
    assert "supported_study_coverage" in evaluation_payload["review_evidence_coverage"]["shortfalls"]


def test_review_pack_partial_always_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    async def _fake_lit(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "success": True,
            "evidence_coverage_passed": True,
            "coverage_summary": "Evidence coverage passed.",
            "coverage_report_path": "runtime/literature/coverage_report.json",
            "outputs": {
                "study_cards_jsonl": "runtime/literature/study_cards.jsonl",
                "coverage_report_json": "runtime/literature/coverage_report.json",
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


def test_review_pack_blocks_when_evidence_coverage_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    async def _fake_lit(*args, **kwargs):
        _ = (args, kwargs)
        return {
            "success": True,
            "evidence_coverage_passed": False,
            "coverage_summary": "Evidence coverage blocked: only 4 included studies.",
            "coverage_report_path": "runtime/literature/coverage_report.json",
            "outputs": {
                "library_jsonl": "runtime/literature/library.jsonl",
                "study_cards_jsonl": "runtime/literature/study_cards.jsonl",
                "coverage_report_json": "runtime/literature/coverage_report.json",
                "references_bib": "runtime/literature/references.bib",
                "evidence_md": "runtime/literature/evidence.md",
                "evidence_coverage_md": "runtime/literature/docs/evidence_coverage.md",
                "study_matrix_md": "runtime/literature/docs/study_matrix.md",
            },
        }

    async def _fail_draft(*args, **kwargs):
        raise AssertionError("manuscript_writer should not run when evidence coverage fails")

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _fake_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _fail_draft)

    result = asyncio.run(review_pack_writer_module.review_pack_writer_handler(topic="Sparse topic"))

    assert result["success"] is False
    assert result["error_code"] == "low_evidence_coverage"
    assert result["public_release_ready"] is False
    assert result["evidence_coverage_passed"] is False
    assert result["draft"] is None
    assert "only 4 included studies" in result["release_summary"]


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
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is True
    output_dir = result["output_dir"]
    assert output_dir.startswith("runtime/session_demo/tool_outputs/literature_pipeline/review_pack_")
    assert (tmp_path / output_dir / "library.jsonl").exists()
    assert (tmp_path / output_dir / "study_cards.jsonl").exists()
    assert (tmp_path / output_dir / "coverage_report.json").exists()
    assert (tmp_path / output_dir / "references.bib").exists()
    assert (tmp_path / output_dir / "evidence.md").exists()
    assert (tmp_path / output_dir / "docs" / "evidence_coverage.md").exists()
    assert (tmp_path / output_dir / "docs" / "study_matrix.md").exists()


def test_literature_pipeline_falls_back_from_zero_result_natural_language_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

    captured_terms: list[str] = []

    async def _fake_esearch(_client, query: str, retmax: int):
        _ = retmax
        captured_terms.append(query)
        if query == "Pseudomonas phage biology, genomics, host interaction, therapeutic applications, and experimental models":
            return []
        return ["1"]

    async def _fake_efetch(_client, _pmids):
        return (
            "<PubmedArticleSet>"
            "<PubmedArticle>"
            "<MedlineCitation>"
            "<PMID>1</PMID>"
            "<Article>"
            "<ArticleTitle>Known phage study</ArticleTitle>"
            "<Abstract><AbstractText>Grounded abstract with 2 log CFU reduction in a murine model.</AbstractText></Abstract>"
            "<Journal><Title>Virology</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>"
            "<AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author></AuthorList>"
            "</Article>"
            "</MedlineCitation>"
            "<PubmedData><ArticleIdList><ArticleId IdType=\"doi\">10.1/example</ArticleId></ArticleIdList></PubmedData>"
            "</PubmedArticle>"
            "</PubmedArticleSet>"
        )

    monkeypatch.setattr(literature_pipeline_module, "_pubmed_esearch", _fake_esearch)
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_efetch_xml", _fake_efetch)

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="Pseudomonas phage biology, genomics, host interaction, therapeutic applications, and experimental models",
            max_results=5,
            download_pdfs=False,
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is True
    assert len(captured_terms) == 2
    assert result["fallback_query_used"]
    assert result["effective_query"] == result["fallback_query_used"]
    assert result["counts"]["records"] == 1


def test_literature_pipeline_redirects_single_component_out_dir_under_runtime_lit_reviews(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

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

    monkeypatch.setattr(literature_pipeline_module, "_pubmed_esearch", _fake_esearch)
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_efetch_xml", _fake_efetch)

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="phage host interaction",
            max_results=2,
            out_dir="plan68_task14",
            download_pdfs=False,
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is True
    assert result["output_dir"] == "runtime/lit_reviews/plan68_task14"
    assert (tmp_path / "runtime" / "lit_reviews" / "plan68_task14" / "library.jsonl").exists()
    assert not (tmp_path / "plan68_task14").exists()


def test_literature_pipeline_redirects_nested_relative_out_dir_under_runtime_lit_reviews(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

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

    monkeypatch.setattr(literature_pipeline_module, "_pubmed_esearch", _fake_esearch)
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_efetch_xml", _fake_efetch)

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="phage host interaction",
            max_results=2,
            out_dir="nested/review_pack_a",
            download_pdfs=False,
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is True
    assert result["output_dir"] == "runtime/lit_reviews/nested/review_pack_a"
    assert (tmp_path / "runtime" / "lit_reviews" / "nested" / "review_pack_a" / "library.jsonl").exists()
    assert not (tmp_path / "nested").exists()


def test_literature_pipeline_rejects_same_prefix_absolute_out_dir_outside_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

    outside_dir = tmp_path.parent / f"{tmp_path.name}-escape" / "lit_review_outside"

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="phage host interaction",
            out_dir=str(outside_dir),
            download_pdfs=False,
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is False
    assert result["error"] == "out_dir_outside_project"
    assert not outside_dir.exists()


def test_review_pack_writer_redirects_single_component_out_dir_under_runtime_lit_reviews(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    captured: Dict[str, Any] = {}

    async def _fake_lit(*args, **kwargs):
        _ = args
        captured["out_dir"] = kwargs.get("out_dir")
        return {
            "success": True,
            "evidence_coverage_passed": True,
            "coverage_summary": "Evidence coverage passed.",
            "coverage_report_path": "runtime/lit_reviews/lit_review_general/coverage_report.json",
            "outputs": {
                "study_cards_jsonl": "runtime/lit_reviews/lit_review_general/study_cards.jsonl",
                "coverage_report_json": "runtime/lit_reviews/lit_review_general/coverage_report.json",
                "evidence_md": "runtime/lit_reviews/lit_review_general/evidence.md",
                "references_bib": "runtime/lit_reviews/lit_review_general/references.bib",
            },
        }

    async def _fake_draft(*args, **kwargs):
        _ = args
        captured["output_path"] = kwargs.get("output_path")
        return {
            "success": True,
            "output_path": kwargs.get("output_path"),
            "quality_gate_passed": True,
            "polish_gate_passed": True,
            "public_release_ready": True,
            "release_state": "ready",
            "release_summary": "Ready.",
        }

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _fake_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _fake_draft)

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Test topic",
            out_dir="lit_review_general",
        )
    )

    assert result["success"] is True
    assert captured["out_dir"] == str((tmp_path / "runtime" / "lit_reviews" / "lit_review_general").resolve())
    assert captured["output_path"] == str(
        (tmp_path / "runtime" / "lit_reviews" / "lit_review_general" / "review_draft.md").resolve()
    )


def test_review_pack_writer_redirects_nested_relative_paths_under_runtime_lit_reviews(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    captured: Dict[str, Any] = {}

    async def _fake_lit(*args, **kwargs):
        _ = args
        captured["out_dir"] = kwargs.get("out_dir")
        return {
            "success": True,
            "evidence_coverage_passed": True,
            "coverage_summary": "Evidence coverage passed.",
            "coverage_report_path": "runtime/lit_reviews/nested/review_pack_b/coverage_report.json",
            "outputs": {
                "study_cards_jsonl": "runtime/lit_reviews/nested/review_pack_b/study_cards.jsonl",
                "coverage_report_json": "runtime/lit_reviews/nested/review_pack_b/coverage_report.json",
                "evidence_md": "runtime/lit_reviews/nested/review_pack_b/evidence.md",
                "references_bib": "runtime/lit_reviews/nested/review_pack_b/references.bib",
            },
        }

    async def _fake_draft(*args, **kwargs):
        _ = args
        captured["output_path"] = kwargs.get("output_path")
        return {
            "success": True,
            "output_path": kwargs.get("output_path"),
            "quality_gate_passed": True,
            "polish_gate_passed": True,
            "public_release_ready": True,
            "release_state": "ready",
            "release_summary": "Ready.",
        }

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _fake_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _fake_draft)

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Test topic",
            out_dir="nested/review_pack_b",
            output_path="drafts/review_b.md",
        )
    )

    assert result["success"] is True
    assert captured["out_dir"] == str(
        (tmp_path / "runtime" / "lit_reviews" / "nested" / "review_pack_b").resolve()
    )
    assert captured["output_path"] == str(
        (tmp_path / "runtime" / "lit_reviews" / "drafts" / "review_b.md").resolve()
    )


def test_review_pack_writer_default_paths_are_passed_to_downstream_as_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    captured: Dict[str, Any] = {}

    async def _fake_lit(*args, **kwargs):
        _ = args
        captured["out_dir"] = kwargs.get("out_dir")
        return {
            "success": True,
            "evidence_coverage_passed": True,
            "coverage_summary": "Evidence coverage passed.",
            "coverage_report_path": "runtime/literature/review_pack_x/coverage_report.json",
            "outputs": {
                "study_cards_jsonl": "runtime/literature/review_pack_x/study_cards.jsonl",
                "coverage_report_json": "runtime/literature/review_pack_x/coverage_report.json",
                "evidence_md": "runtime/literature/review_pack_x/evidence.md",
                "references_bib": "runtime/literature/review_pack_x/references.bib",
            },
        }

    async def _fake_draft(*args, **kwargs):
        _ = args
        captured["output_path"] = kwargs.get("output_path")
        return {
            "success": True,
            "output_path": kwargs.get("output_path"),
            "quality_gate_passed": True,
            "polish_gate_passed": True,
            "public_release_ready": True,
            "release_state": "ready",
            "release_summary": "Ready.",
        }

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _fake_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _fake_draft)

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Test topic",
        )
    )

    assert result["success"] is True
    lit_out_dir = Path(captured["out_dir"])
    draft_output_path = Path(captured["output_path"])
    assert lit_out_dir.is_absolute()
    assert lit_out_dir.parent == (tmp_path / "runtime" / "literature")
    assert lit_out_dir.name.startswith("review_pack_")
    assert draft_output_path.is_absolute()
    assert draft_output_path.parent == lit_out_dir
    assert draft_output_path.name == "review_draft.md"


def test_review_pack_writer_rejects_same_prefix_absolute_out_dir_outside_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    async def _unexpected_lit(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("literature_pipeline_handler should not be called")

    async def _unexpected_draft(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("manuscript_writer_handler should not be called")

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _unexpected_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _unexpected_draft)

    outside_dir = tmp_path.parent / f"{tmp_path.name}-escape" / "review_pack_outside"

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Test topic",
            out_dir=str(outside_dir),
        )
    )

    assert result["success"] is False
    assert result["error"] == "out_dir_outside_project"
    assert not outside_dir.exists()


def test_review_pack_writer_rejects_absolute_output_path_outside_project_before_running_downstream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(review_pack_writer_module, "_PROJECT_ROOT", tmp_path)

    async def _unexpected_lit(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("literature_pipeline_handler should not be called")

    async def _unexpected_draft(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("manuscript_writer_handler should not be called")

    monkeypatch.setattr(review_pack_writer_module, "literature_pipeline_handler", _unexpected_lit)
    monkeypatch.setattr(review_pack_writer_module, "manuscript_writer_handler", _unexpected_draft)

    outside_file = tmp_path.parent / f"{tmp_path.name}-escape" / "review_draft.md"

    result = asyncio.run(
        review_pack_writer_module.review_pack_writer_handler(
            topic="Test topic",
            output_path=str(outside_file),
        )
    )

    assert result["success"] is False
    assert result["error"] == "output_path_outside_project"
    assert not outside_file.exists()



def test_download_pmc_pdf_uses_oa_package_full_text(
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    package_buf = io.BytesIO()
    with tarfile.open(fileobj=package_buf, mode="w:gz") as archive:
        body = (
            b"<article><body><sec><title>Results</title>"
            b"<p>The phage reduced bacterial burden by 2 log CFU in murine infection models.</p>"
            b"</sec></body></article>"
        )
        info = tarfile.TarInfo(name="PMC123456/article.nxml")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    package_bytes = package_buf.getvalue()

    class _Response:
        def __init__(self, *, text: str = "", content: bytes = b"", status_code: int = 200, headers: dict[str, str] | None = None):
            self.text = text
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}

    class _Client:
        async def get(self, url: str, **kwargs):
            _ = kwargs
            if "oa.fcgi" in url:
                return _Response(
                    text=(
                        '<OA><records><record id="PMC123456">'
                        '<link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/aa/bb/PMC123456.tar.gz" />'
                        "</record></records></OA>"
                    )
                )
            if "PMC123456.tar.gz" in url:
                return _Response(content=package_bytes, headers={"content-type": "application/gzip"})
            raise AssertionError(f"Unexpected URL: {url}")

    ok, err, full_text = asyncio.run(
        literature_pipeline_module._download_pmc_pdf(_Client(), "PMC123456", tmp_path / "article.pdf")
    )

    assert ok is True
    assert err is None
    assert full_text is not None
    assert "2 log CFU" in full_text
    assert not (tmp_path / "article.pdf").exists()


def test_literature_pipeline_counts_oa_fulltext_without_pdf_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

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
            "<PubmedData><ArticleIdList>"
            "<ArticleId IdType=\"doi\">10.1/example</ArticleId>"
            "<ArticleId IdType=\"pmc\">PMC123456</ArticleId>"
            "</ArticleIdList></PubmedData>"
            "</PubmedArticle>"
            "</PubmedArticleSet>"
        )

    async def _fake_download(_client, _pmcid: str, _out_path: Path):
        return True, None, "Full text reports a 2 log CFU reduction after treatment in a murine model."

    monkeypatch.setattr(literature_pipeline_module, "_pubmed_esearch", _fake_esearch)
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_efetch_xml", _fake_efetch)
    monkeypatch.setattr(literature_pipeline_module, "_download_pmc_pdf", _fake_download)

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="phage host interaction",
            max_results=2,
            download_pdfs=True,
            max_pdfs=2,
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is True
    assert result["counts"]["full_text_study_cards"] == 1
    study_cards = [
        json.loads(line)
        for line in (tmp_path / result["output_dir"] / "study_cards.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert study_cards[0]["evidence_tier"] == "full_text"


def test_literature_pipeline_tolerates_pmc_download_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tool_box.tools_impl import literature_pipeline as literature_pipeline_module

    monkeypatch.setattr(literature_pipeline_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(literature_pipeline_module, "_RUNTIME_DIR", tmp_path / "runtime")

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
            "<PubmedData><ArticleIdList>"
            "<ArticleId IdType=\"doi\">10.1/example</ArticleId>"
            "<ArticleId IdType=\"pmc\">PMC123456</ArticleId>"
            "</ArticleIdList></PubmedData>"
            "</PubmedArticle>"
            "</PubmedArticleSet>"
        )

    async def _fake_download(_client, _pmcid: str, _out_path: Path):
        raise RuntimeError("Server disconnected without sending a response.")

    monkeypatch.setattr(literature_pipeline_module, "_pubmed_esearch", _fake_esearch)
    monkeypatch.setattr(literature_pipeline_module, "_pubmed_efetch_xml", _fake_efetch)
    monkeypatch.setattr(literature_pipeline_module, "_download_pmc_pdf", _fake_download)

    result = asyncio.run(
        literature_pipeline_module.literature_pipeline_handler(
            query="phage host interaction",
            max_results=2,
            download_pdfs=True,
            max_pdfs=1,
            include_europepmc=False,
            include_biorxiv=False,
        )
    )

    assert result["success"] is True
    assert result["counts"]["records"] == 1
    assert result["counts"]["full_text_study_cards"] == 0
    assert any("Server disconnected without sending a response." in err for err in result["errors"])


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
            "evidence_coverage_passed": True,
            "coverage_summary": "Evidence coverage passed.",
            "coverage_report_path": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/coverage_report.json",
            "outputs": {
                "study_cards_jsonl": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/study_cards.jsonl",
                "coverage_report_json": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/coverage_report.json",
                "evidence_md": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/evidence.md",
                "references_bib": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/references.bib",
                "evidence_coverage_md": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/docs/evidence_coverage.md",
                "study_matrix_md": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/docs/study_matrix.md",
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
            "evidence_coverage_passed": True,
            "coverage_summary": "Evidence coverage passed.",
            "coverage_report_path": "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/coverage_report.json",
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
    assert result["evidence_coverage_passed"] is True
    assert result["release_state"] == "final"
    assert captured["lit"]["session_id"] == "demo"
    assert captured["draft"]["session_id"] == "demo"
    assert captured["lit"]["download_pdfs"] is True
    lit_out_dir = Path(captured["lit"]["out_dir"])
    assert lit_out_dir.is_absolute()
    assert lit_out_dir.parent == (
        tmp_path / "runtime" / "session_demo" / "tool_outputs" / "review_pack_writer"
    )
    assert lit_out_dir.name.startswith("review_pack_")
    assert captured["draft"]["context_paths"][:2] == [
        "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/study_cards.jsonl",
        "runtime/session_demo/tool_outputs/review_pack_writer/review_pack_20260311_000000/coverage_report.json",
    ]
    draft_output_path = Path(captured["draft"]["output_path"])
    assert draft_output_path.is_absolute()
    assert draft_output_path.parent == lit_out_dir
    assert draft_output_path.name == "review_draft.md"
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


def test_manuscript_writer_exemplar_style_in_section_and_revision_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANUSCRIPT_EXEMPLAR_STYLE_ENABLED", raising=False)
    requirements = manuscript_writer_module._section_requirements("introduction", review_mode=True)
    section_prompt = manuscript_writer_module._build_section_prompt(
        "Write a submission-ready English review article.",
        "introduction",
        "# Analysis Memo",
        "context",
        requirements,
        review_mode=True,
    )
    revision_prompt = manuscript_writer_module._build_revision_prompt(
        "introduction",
        "# Analysis Memo",
        "context",
        "## Introduction\nText",
        {"scores": {"structure": 0.7}, "defects": [], "revision_instructions": []},
        requirements,
        review_mode=True,
    )
    assert "Style exemplars" in section_prompt
    assert "Nature Reviews MCB" in section_prompt
    assert "Style exemplars" in revision_prompt


def test_manuscript_writer_exemplar_style_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANUSCRIPT_EXEMPLAR_STYLE_ENABLED", "0")
    requirements = manuscript_writer_module._section_requirements("abstract", review_mode=False)
    section_prompt = manuscript_writer_module._build_section_prompt(
        "Task",
        "abstract",
        "# Memo",
        "ctx",
        requirements,
        review_mode=False,
    )
    assert "Style exemplars" not in section_prompt


def test_manuscript_writer_merge_prompt_includes_optional_exemplar_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANUSCRIPT_EXEMPLAR_STYLE_ENABLED", raising=False)
    merge_prompt = manuscript_writer_module._build_merge_prompt("task", "memo", "combined")
    assert "Nature-tier" in merge_prompt or "nature_exemplars" in merge_prompt.lower()


def test_exemplar_review_mode_body_weights_r1_r3_not_r2_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aligns with docs: review synthesis favors R1+R3 in method/experiment/result; R2 mainly abstract."""
    monkeypatch.delenv("MANUSCRIPT_EXEMPLAR_STYLE_ENABLED", raising=False)
    method_r = manuscript_writer_module._exemplar_style_instructions("method", review_mode=True)
    assert "Primary: R1" in method_r
    assert "Nature Reviews MCB" in method_r.split("Primary:")[1][:200]
    exp_r = manuscript_writer_module._exemplar_style_instructions("experiment", review_mode=True)
    assert "Primary: R3" in exp_r
    res_r = manuscript_writer_module._exemplar_style_instructions("result", review_mode=True)
    assert "Primary: R1" in res_r
    assert "Optional compactness: R2" in res_r or "R2" in res_r
    method_a = manuscript_writer_module._exemplar_style_instructions("method", review_mode=False)
    assert "Primary: R2" in method_a
