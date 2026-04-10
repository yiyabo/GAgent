from __future__ import annotations

from datetime import datetime, timezone

from app.routers.chat.agent import _build_deep_think_response_metadata
from app.services.deep_think_agent import DeepThinkResult, ThinkingStep


def _thinking_step() -> ThinkingStep:
    now = datetime.now(timezone.utc)
    return ThinkingStep(
        iteration=1,
        thought="Inspect prior outputs",
        action=None,
        action_result=None,
        self_correction=None,
        display_text="Inspecting previous outputs",
        kind="summary",
        status="done",
        timestamp=now,
        started_at=now,
        finished_at=now,
    )


def test_build_deep_think_response_metadata_persists_refreshable_fields() -> None:
    result = DeepThinkResult(
        final_answer="The report is ready.",
        thinking_steps=[_thinking_step()],
        total_iterations=1,
        tools_used=["document_reader"],
        confidence=0.82,
        thinking_summary="Reviewed prior outputs and summarized them.",
    )

    metadata = _build_deep_think_response_metadata(
        result=result,
        routing_metadata={"thinking_visibility": "progress"},
        plan_id=42,
        plan_title="Plan 42",
        reasoning_language="en",
        thinking_visible=False,
        progress_visible=True,
        artifact_gallery=[
            {
                "path": "results/report.png",
                "display_name": "report.png",
                "source_tool": "document_reader",
                "mime_family": "image",
                "origin": "tool_output",
            }
        ],
        tool_results=[
            {
                "name": "document_reader",
                "summary": "Read the generated report",
                "parameters": {"path": "results/report.md"},
                "result": {"success": True, "query": "results/report.md"},
            }
        ],
        deep_think_job_id="dt_test_1",
        background_category="code_executor",
        display_text="The report is ready.",
        structured_plan_meta={"plan_creation_state": "created"},
        plan_runtime_meta={"plan_evaluation": {"score": 0.9}},
    )

    assert metadata["status"] == "completed"
    assert metadata["unified_stream"] is True
    assert metadata["analysis_text"] == "The report is ready."
    assert metadata["final_summary"] == "The report is ready."
    assert metadata["background_category"] == "code_executor"
    assert metadata["deep_think_job_id"] == "dt_test_1"
    assert metadata["tool_results"][0]["name"] == "document_reader"
    assert metadata["artifact_gallery"][0]["path"] == "results/report.png"
    assert metadata["thinking_process"]["status"] == "completed"
    assert metadata["thinking_process"]["steps"][0]["status"] == "done"


def test_build_deep_think_response_metadata_prefers_latest_review_evaluation() -> None:
    result = DeepThinkResult(
        final_answer="Plan update failed after retries.",
        thinking_steps=[_thinking_step()],
        total_iterations=2,
        tools_used=["plan_operation"],
        confidence=0.3,
        thinking_summary="Reviewed the plan before timing out.",
        fallback_used=True,
    )

    metadata = _build_deep_think_response_metadata(
        result=result,
        routing_metadata={"thinking_visibility": "progress"},
        plan_id=65,
        plan_title="Plan 65",
        reasoning_language="en",
        thinking_visible=False,
        progress_visible=True,
        tool_results=[
            {
                "name": "plan_operation",
                "summary": "Plan review complete.",
                "parameters": {"operation": "review", "plan_id": 65},
                "result": {
                    "success": True,
                    "operation": "review",
                    "plan_id": 65,
                    "rubric_score": 73.2,
                    "rubric_dimension_scores": {"accuracy": 98.0},
                    "rubric_subcriteria_scores": {"accuracy": {"A1": 1.0}},
                    "rubric_feedback": {"weaknesses": ["Needs more explicit IO contracts."]},
                    "rubric_evaluator": {
                        "provider": "qwen",
                        "model": "qwen3.6-plus",
                        "rubric_version": "plan_rubric_v1",
                        "evaluated_at": "2026-04-09T09:15:33.785347Z",
                    },
                },
            }
        ],
        display_text="Plan update failed after retries.",
        structured_plan_meta={"plan_creation_state": "failed"},
        plan_runtime_meta={"plan_evaluation": {"overall_score": 45.6}},
    )

    assert metadata["plan_evaluation"]["overall_score"] == 73.2
    assert metadata["plan_evaluation"]["dimension_scores"]["accuracy"] == 98.0
    assert metadata["plan_evaluation"]["evaluator_provider"] == "qwen"
