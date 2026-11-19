"""Automatic, evidence-bounded final delivery for contract-aware Plans.

A successful task queue is not itself a research answer.  This module performs one
integrated synthesis, one domain review, and one bounded revision, then publishes
both the report and review to the owning session's Deliverables.  Every model call
is injectable so tests never require an external provider.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.deliverables.publisher import get_deliverable_publisher
from app.services.session_paths import get_session_raw_files_dir

from .research_contract import research_contract_prompt

logger = logging.getLogger(__name__)

_TASK_CHAR_LIMIT = 8000
_TOTAL_EVIDENCE_CHAR_LIMIT = 120_000
_REPORT_CHAR_LIMIT = 60_000
_SUMMARY_CHAR_LIMIT = 6000

_SYSTEM = """You are the final research integrator for a completed Plan.
Write a comprehensive answer to the original Research Contract, not a task-status summary.
Use only the supplied task evidence. Preserve quantitative details, provenance, contrary evidence,
and limitations. Cite supporting task IDs and concrete artifact paths where available.
Do not invent references, results, validation, or publication readiness.
The report must contain: research question, methods, integrated findings, interpretation,
limitations/threats to validity, and a deliverables section.
Domain review is mandatory: correlation is not causation; database annotations are not independent
experimental validation; host taxonomy differences alone do not establish independent phage lineages.
Respond in the language of the original research objective.
"""

_REVIEW_SYSTEM = """You are an independent domain reviewer. Audit the draft against the original
Research Contract and task evidence. Identify unsupported causal or evolutionary claims, missing
provenance, contradictions, overclaimed validation/publication readiness, and requested deliverables
that are absent. Be specific and concise. Do not invent new evidence or rewrite the report.
"""


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    marker = "\n...[truncated]...\n"
    available = limit - len(marker)
    if available < 2:
        return value[:max(0, limit)]
    head = available * 2 // 3
    tail = available - head
    return f"{value[:head]}{marker}{value[-tail:]}"


def _result_content(node: Any) -> str:
    raw = str(getattr(node, "execution_result", "") or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if isinstance(payload, dict):
        result_fields = [payload[key] for key in ("content", "result", "summary") if key in payload]
        for content in result_fields:
            if isinstance(content, str):
                if content.strip():
                    return content.strip()
            elif content is not None and content != {} and content != []:
                return raw
        if result_fields or not payload:
            return ""
    if isinstance(payload, str):
        return payload.strip()
    return "" if payload is None or payload == [] else raw


def _artifact_paths(node: Any) -> list[str]:
    raw = str(getattr(node, "execution_result", "") or "").strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_metadata = payload.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    candidates = metadata.get("artifact_paths") or payload.get("artifact_paths") or []
    if not isinstance(candidates, list):
        return []
    return [str(item) for item in candidates if isinstance(item, str) and item.strip()][:20]


def collect_task_evidence(tree: Any) -> str:
    """Collect completed leaf evidence, excluding composite auto-completion placeholders."""
    sections: list[str] = []
    remaining = _TOTAL_EVIDENCE_CHAR_LIMIT
    for node in tree.ordered_nodes():
        if tree.children_ids(node.id):
            continue
        status = str(node.status or "").lower()
        if status not in {"completed", "done"}:
            continue
        content = _result_content(node)
        if not content:
            continue
        if sections:
            remaining -= 2  # Inter-task separator.
        if remaining <= 0:
            break
        section = "\n".join(
            [
                f"=== TASK {node.id}: {node.display_name()} [{status}] ===",
                f"Instruction: {_clip(node.instruction, 1200)}",
                f"Artifacts: {json.dumps(_artifact_paths(node), ensure_ascii=False)}",
                f"Result:\n{_clip(content, min(_TASK_CHAR_LIMIT, remaining))}",
            ]
        )
        section = _clip(section, remaining)
        sections.append(section)
        remaining -= len(section)
    return "\n\n".join(sections)


def _prompt(contract_prompt: str, evidence: str, instruction: str) -> str:
    return f"{contract_prompt}\n\n=== COMPLETED TASK EVIDENCE ===\n{evidence}\n\n{instruction}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def generate_plan_delivery(
    *,
    repo: Any,
    plan_id: int,
    session_id: str | None,
    job_id: str | None = None,
    llm_chat: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Synthesize and publish a final report; never turn a failed queue into success."""
    tree = repo.get_plan_tree(plan_id)
    contract = tree.metadata.get("research_contract") if isinstance(tree.metadata, dict) else None
    if not isinstance(contract, dict):
        return {"status": "skipped", "reason": "legacy_plan_without_research_contract"}
    evidence = collect_task_evidence(tree)
    if not evidence:
        delivery = {"status": "failed", "error": "no_completed_leaf_evidence"}
        repo.update_plan_metadata(plan_id, {"delivery": delivery})
        return delivery
    contract_prompt = research_contract_prompt(contract)
    if llm_chat is None:
        from .executor_llm import PlanExecutorLLMService

        llm_chat = PlanExecutorLLMService()._llm.chat

    delivery: dict[str, Any] = {"status": "running", "plan_id": plan_id}
    try:
        draft = llm_chat(
            _prompt(
                contract_prompt,
                evidence,
                f"{_SYSTEM}\nWrite the first integrated report now.",
            ),
            max_tokens=12_000,
            timeout=300,
        ).strip()
        if not draft:
            raise ValueError("empty_integrated_report")
        delivery["draft_chars"] = len(draft)
    except Exception as exc:
        logger.warning("Plan %s integrated synthesis failed: %s", plan_id, exc)
        delivery.update(status="failed", error=f"integrated_synthesis:{type(exc).__name__}")
        repo.update_plan_metadata(plan_id, {"delivery": delivery})
        return delivery

    review = ""
    try:
        review = llm_chat(
            _prompt(
                contract_prompt,
                evidence,
                f"{_REVIEW_SYSTEM}\n=== DRAFT ===\n{_clip(draft, _REPORT_CHAR_LIMIT)}",
            ),
            max_tokens=4000,
            timeout=240,
        ).strip()
        if not review:
            raise ValueError("empty_domain_review")
        delivery["review_status"] = "completed"
    except Exception as exc:
        logger.warning("Plan %s domain review failed: %s", plan_id, exc)
        delivery.update(review_status="failed", review_error=type(exc).__name__)

    report = draft
    delivery["revision_status"] = "skipped"
    if review:
        try:
            revised = llm_chat(
                _prompt(
                    contract_prompt,
                    evidence,
                    f"{_SYSTEM}\n=== INDEPENDENT DOMAIN REVIEW ===\n{_clip(review, 16_000)}\n"
                    f"=== DRAFT TO REVISE ===\n{_clip(draft, _REPORT_CHAR_LIMIT)}\n"
                    "Resolve every valid review finding. If evidence is insufficient, narrow the claim; "
                    "do not merely add a disclaimer. Return only the final report.",
                ),
                max_tokens=12_000,
                timeout=300,
            ).strip()
            if not revised:
                raise ValueError("empty_final_revision")
            report = revised
            delivery["revision_status"] = "completed"
        except Exception as exc:
            logger.warning("Plan %s final revision failed; retaining reviewed draft: %s", plan_id, exc)
            delivery["revision_status"] = "failed"

    delivery["report_chars"] = len(report)
    if session_id:
        try:
            root = (get_session_raw_files_dir(session_id, create=True) / "tmp" / "plan_delivery" / f"plan_{plan_id}").resolve()
            report_name = f"plan_{plan_id}_final_report.md"
            review_name = f"plan_{plan_id}_domain_review.md"
            report_source = root / report_name
            review_source = root / review_name
            _write(report_source, report)
            artifacts: list[dict[str, str | bool]] = [
                {"path": str(report_source), "module": "docs", "reason": "Integrated Plan research report"}
            ]
            if review:
                _write(review_source, review)
                artifacts.append(
                    # This server-generated filename also needs to survive the docs cleanup policy.
                    {"path": str(review_source), "module": "docs", "reason": "Independent domain review", "trusted": True}
                )
            publish_report = get_deliverable_publisher().publish_from_tool_result(
                session_id=session_id,
                tool_name="plan_delivery",
                raw_result={"success": True, "deliverable_submit": {"publish": True, "artifacts": artifacts}},
                summary="Integrated Plan research delivery",
                job_id=job_id,
                plan_id=plan_id,
                task_name=tree.title,
            )
            if publish_report is None:
                delivery["publish_status"] = "disabled"
            else:
                published_paths = {item.source_path: item.path for item in publish_report.published_artifacts}
                report_path = published_paths.get(str(report_source))
                review_path = published_paths.get(str(review_source)) if review else None
                if report_path:
                    delivery["report_path"] = f"deliverables/latest/{report_path}"
                if review_path:
                    delivery["review_path"] = f"deliverables/latest/{review_path}"
                if report_path or review_path:
                    delivery["session_id"] = session_id
                if report_path and (not review or review_path):
                    delivery["publish_status"] = "completed"
                elif report_path or review_path:
                    delivery["publish_status"] = "partial"
                else:
                    delivery["publish_status"] = "failed"
        except Exception as exc:
            logger.warning("Plan %s delivery publication failed: %s", plan_id, exc)
            delivery.update(publish_status="failed", publish_error=type(exc).__name__)
    else:
        delivery.update(publish_status="skipped", publish_error="missing_delivery_session")

    if not delivery.get("report_path"):
        delivery.update(status="failed", error="report_not_published")
    elif (
        delivery.get("review_path")
        and delivery.get("review_status") == "completed"
        and delivery.get("revision_status") == "completed"
        and delivery.get("publish_status") == "completed"
    ):
        delivery["status"] = "completed"
    else:
        delivery["status"] = "partial"

    delivery["summary"] = _clip(report, _SUMMARY_CHAR_LIMIT)
    delivery["content"] = report
    delivery["review"] = review
    delivery["generated_at"] = datetime.now(timezone.utc).isoformat()
    metadata_delivery = {key: value for key, value in delivery.items() if key not in {"content", "review"}}
    repo.update_plan_metadata(plan_id, {"delivery": metadata_delivery})
    return delivery
