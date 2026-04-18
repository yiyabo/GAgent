from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from ...config import get_graph_rag_settings, get_search_settings
from ...repository.plan_repository import PlanRepository
from ..llm.decomposer_service import strip_code_fences
from .plan_decomposer import DecompositionResult, PlanDecomposer
from .plan_models import PlanNode, PlanTree

logger = logging.getLogger(__name__)


class PlanMaterialDecision(BaseModel):
    use_external_context: bool = False
    tool_name: Optional[str] = None
    query: Optional[str] = None


@dataclass
class PlanGenerationOutcome:
    plan_tree: PlanTree
    root_task_id: Optional[int]
    seeded_tasks: List[PlanNode]
    decomposition: Optional[DecompositionResult]
    collected_materials: List[Dict[str, Any]]
    session_context: Optional[Dict[str, Any]]
    decomposition_status: str
    auto_completed_generation: bool = True


SEARCH_DECISION_HEADER = (
    "You are a plan generation research decision assistant. Decide whether external retrieval is "
    "required before creating and decomposing a structured execution plan. Return only JSON."
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _append_recent_tool_result(
    session_context: Optional[Dict[str, Any]],
    *,
    tool_name: str,
    summary: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    effective = dict(session_context or {})
    recent = effective.get("recent_tool_results")
    if not isinstance(recent, list):
        recent = []
    entry: Dict[str, Any] = {
        "tool": tool_name,
        "summary": _safe_text(summary, limit=1200),
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            entry[key] = value
    recent.append(entry)
    effective["recent_tool_results"] = recent[-6:]
    return effective


def _normalise_task_seed_preview(tasks: Optional[List[Dict[str, Any]]]) -> List[str]:
    lines: List[str] = []
    if not isinstance(tasks, list):
        return lines
    for index, task in enumerate(tasks[:8], start=1):
        if not isinstance(task, dict):
            continue
        name = str(task.get("name") or task.get("title") or f"Task {index}").strip()
        instruction = str(task.get("instruction") or task.get("description") or "").strip()
        if len(instruction) > 120:
            instruction = instruction[:117].rstrip() + "..."
        line = f"- {name}"
        if instruction:
            line += f" :: {instruction}"
        lines.append(line)
    return lines


def _build_plan_material_decision_prompt(
    *,
    title: str,
    description: Optional[str],
    tasks: Optional[List[Dict[str, Any]]],
    session_context: Optional[Dict[str, Any]],
) -> str:
    effective = session_context or {}
    prompt: List[str] = [
        SEARCH_DECISION_HEADER,
        "\n=== TARGET PLAN ===",
        f"Title: {title}",
        f"Description: {description or ''}",
    ]

    user_message = str(effective.get("user_message") or "").strip()
    if user_message:
        prompt.extend([
            "\n=== USER REQUEST ===",
            user_message,
        ])

    seed_preview = _normalise_task_seed_preview(tasks)
    if seed_preview:
        prompt.extend([
            "\n=== EXISTING SEEDED TASKS ===",
            *seed_preview,
        ])

    chat_history = effective.get("chat_history")
    if isinstance(chat_history, list) and chat_history:
        prompt.append("\n=== RECENT CONVERSATION ===")
        for item in chat_history[-4:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "unknown").strip() or "unknown"
            content = _safe_text(item.get("content"), limit=360)
            if content:
                prompt.append(f"[{role}] {content}")

    recent_tool_results = effective.get("recent_tool_results")
    if isinstance(recent_tool_results, list) and recent_tool_results:
        prompt.append("\n=== EXISTING TOOL EVIDENCE ===")
        for item in recent_tool_results[-3:]:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool") or item.get("name") or "unknown").strip()
            summary = _safe_text(item.get("summary"), limit=300)
            if summary:
                prompt.append(f"- {tool_name}: {summary}")

    prompt.extend([
        "\n=== RESPONSE FORMAT ===",
        "{",
        '  "use_external_context": <true|false>,',
        '  "tool_name": "web_search" | "graph_rag" | "none",',
        '  "query": "<string>"',
        "}",
        "\nSTRICT REQUIREMENTS:",
        "- Return only valid JSON with exactly these keys.",
        "- If existing tool evidence already covers the required background, set use_external_context=false.",
        "- Use graph_rag for phage/phage-host/taxonomy/host-range/viral knowledge graph style questions.",
        "- Use web_search for latest best practices, recent methods, external benchmarks, version-sensitive tools, and current literature guidance.",
        "- If external retrieval is unnecessary, set tool_name='none' and query=''.",
        "- Keep query concise and concrete (<= 120 chars).",
    ])
    return "\n".join(prompt)


def _parse_plan_material_decision(raw: Any) -> PlanMaterialDecision:
    text = strip_code_fences(str(raw or "").strip())
    if not text:
        return PlanMaterialDecision()
    try:
        payload = json.loads(text)
    except Exception:
        return PlanMaterialDecision()
    if not isinstance(payload, dict):
        return PlanMaterialDecision()
    use_external_context = bool(payload.get("use_external_context"))
    tool_name = str(payload.get("tool_name") or "").strip().lower()
    query = str(payload.get("query") or "").strip()
    if not use_external_context or tool_name not in {"web_search", "graph_rag"} or not query:
        return PlanMaterialDecision(use_external_context=False, tool_name=None, query=None)
    if len(query) > 120:
        query = query[:120].strip()
    return PlanMaterialDecision(
        use_external_context=True,
        tool_name=tool_name,
        query=query,
    )


def _fallback_plan_material_decision(
    *,
    title: str,
    description: Optional[str],
    session_context: Optional[Dict[str, Any]],
) -> PlanMaterialDecision:
    text = "\n".join(
        part
        for part in (
            title,
            description or "",
            str((session_context or {}).get("user_message") or ""),
        )
        if part
    ).lower()
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return PlanMaterialDecision()

    graph_rag_keywords = (
        "phage",
        "bacteriophage",
        "host range",
        "phage-host",
        "virus-host",
        "taxonomy",
        "viral",
        "capsid",
        "tail fiber",
    )
    if any(keyword in normalized for keyword in graph_rag_keywords):
        return PlanMaterialDecision(
            use_external_context=True,
            tool_name="graph_rag",
            query=_safe_text(normalized, limit=120),
        )

    web_search_keywords = (
        "latest",
        "recent",
        "current",
        "best practice",
        "benchmark",
        "version",
        "state of the art",
        "最新",
        "近期",
        "最近",
        "最佳实践",
        "benchmark",
    )
    if any(keyword in normalized for keyword in web_search_keywords):
        return PlanMaterialDecision(
            use_external_context=True,
            tool_name="web_search",
            query=_safe_text(normalized, limit=120),
        )
    return PlanMaterialDecision()


def _format_web_context(
    payload: Dict[str, Any],
    *,
    query: Optional[str] = None,
) -> Tuple[str, int, Optional[str]]:
    summary_raw = str(payload.get("response") or payload.get("answer") or "").strip()
    results = payload.get("results") or []
    if not isinstance(results, list):
        results = []
    provider = str(payload.get("provider") or "").strip() or None

    lines: List[str] = []
    if query:
        lines.append(f"Query: {query}")
    if summary_raw:
        lines.append("Key findings:")
        lines.append(f"- {summary_raw}")
    if results:
        lines.append("Sources:")
        for item in results[:5]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "source").strip()
            url = str(item.get("url") or "").strip()
            snippet = _safe_text(item.get("snippet"), limit=180)
            line = f"- {title}"
            if url:
                line += f" | {url}"
            if snippet:
                line += f" | {snippet}"
            lines.append(line)
    return "\n".join(lines).strip(), len(results), provider


def _format_graph_rag_context(
    payload: Dict[str, Any],
    *,
    query: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return "", {}

    response = str(result.get("response") or "").strip()
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    mode = str(result.get("mode") or payload.get("mode") or "").strip()
    backend = str(result.get("backend") or "").strip()

    lines: List[str] = []
    if query:
        lines.append(f"Query: {query}")
    if response:
        lines.append("GraphRAG findings:")
        lines.append(f"- {response}")
    if trace:
        lines.append("Trace:")
        for key, value in list(trace.items())[:6]:
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            lines.append(f"- {key}: {_safe_text(rendered, limit=180)}")

    return "\n".join(lines).strip(), {
        "query": query or "",
        "mode": mode,
        "backend": backend,
        "trace": trace,
    }


async def collect_plan_generation_materials(
    *,
    title: str,
    description: Optional[str],
    tasks: Optional[List[Dict[str, Any]]],
    session_context: Optional[Dict[str, Any]],
    decomposer: Optional[PlanDecomposer],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    from tool_box import execute_tool

    effective_context = dict(session_context or {})
    if decomposer is None:
        return effective_context or None, []

    llm_service = getattr(decomposer, "_llm", None)
    decision = PlanMaterialDecision()
    if llm_service is not None and hasattr(llm_service, "decide_search"):
        prompt = _build_plan_material_decision_prompt(
            title=title,
            description=description,
            tasks=tasks,
            session_context=effective_context,
        )
        try:
            raw_decision = llm_service.decide_search(prompt)
            decision = _parse_plan_material_decision(raw_decision)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Plan material decision failed for '%s': %s", title, exc)

    if not decision.use_external_context:
        decision = _fallback_plan_material_decision(
            title=title,
            description=description,
            session_context=effective_context,
        )
    if not decision.use_external_context or not decision.tool_name or not decision.query:
        return effective_context or None, []

    materials: List[Dict[str, Any]] = []
    try:
        if decision.tool_name == "graph_rag":
            graph_settings = get_graph_rag_settings()
            payload = await execute_tool(
                "graph_rag",
                query=decision.query,
                top_k=min(12, graph_settings.max_top_k),
                hops=min(1, graph_settings.max_hops),
                return_subgraph=True,
                focus_entities=[],
            )
            summary, meta = _format_graph_rag_context(payload if isinstance(payload, dict) else {}, query=decision.query)
            if summary:
                effective_context = _append_recent_tool_result(
                    effective_context,
                    tool_name="graph_rag",
                    summary=summary,
                    extra={"query": decision.query, "meta": meta},
                )
                materials.append(
                    {
                        "tool": "graph_rag",
                        "query": decision.query,
                        "summary": summary,
                        "meta": meta,
                    }
                )
        else:
            search_settings = get_search_settings()
            payload = await execute_tool(
                "web_search",
                query=decision.query,
                provider=search_settings.default_provider,
                max_results=5,
            )
            summary, results_count, provider = _format_web_context(payload if isinstance(payload, dict) else {}, query=decision.query)
            if summary:
                effective_context = _append_recent_tool_result(
                    effective_context,
                    tool_name="web_search",
                    summary=summary,
                    extra={
                        "query": decision.query,
                        "provider": provider,
                        "results_count": results_count,
                    },
                )
                materials.append(
                    {
                        "tool": "web_search",
                        "query": decision.query,
                        "summary": summary,
                        "provider": provider,
                        "results_count": results_count,
                    }
                )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Plan material collection failed for '%s' using %s: %s",
            title,
            decision.tool_name,
            exc,
        )

    return effective_context or None, materials


def _root_instruction(title: str, description: Optional[str]) -> str:
    return str(description or f"Root task for plan: {title}").strip()


def _create_seed_tasks(
    repo: PlanRepository,
    plan_id: int,
    *,
    root_task_id: int,
    tasks: Optional[List[Dict[str, Any]]],
) -> List[PlanNode]:
    if not isinstance(tasks, list) or not tasks:
        return []

    created_nodes: List[PlanNode] = []
    name_to_id: Dict[str, int] = {}
    pending_dependency_names: List[Tuple[int, List[Any]]] = []

    for index, raw_task in enumerate(tasks, start=1):
        if not isinstance(raw_task, dict):
            continue
        name = str(raw_task.get("name") or raw_task.get("title") or f"Task {index}").strip()
        instruction = str(raw_task.get("instruction") or raw_task.get("description") or "").strip()
        status = str(raw_task.get("status") or "pending").strip() or "pending"
        metadata = dict(raw_task.get("metadata") or {}) if isinstance(raw_task.get("metadata"), dict) else {}
        metadata.setdefault("task_type", metadata.get("task_type") or "composite")

        parent_id = raw_task.get("parent_id")
        try:
            parent_id_int = int(parent_id) if parent_id is not None else root_task_id
        except (TypeError, ValueError):
            parent_id_int = root_task_id
        if parent_id_int is None:
            parent_id_int = root_task_id

        node = repo.create_task(
            plan_id,
            name=name,
            status=status,
            instruction=instruction or None,
            parent_id=parent_id_int,
            metadata=metadata,
        )
        created_nodes.append(node)
        name_to_id[name] = node.id
        pending_dependency_names.append((node.id, list(raw_task.get("dependencies") or [])))

    for task_id, dependency_values in pending_dependency_names:
        dep_ids: List[int] = []
        for value in dependency_values:
            if isinstance(value, int):
                dep_ids.append(value)
                continue
            text = str(value or "").strip()
            if not text:
                continue
            if text.isdigit():
                dep_ids.append(int(text))
                continue
            dep_id = name_to_id.get(text)
            if dep_id is not None:
                dep_ids.append(dep_id)
        deduped = [dep for dep in dep_ids if dep != task_id]
        if deduped:
            repo.update_task(plan_id, task_id, dependencies=deduped)

    return created_nodes


def _build_generation_metadata(
    *,
    plan_tree: PlanTree,
    root_task_id: Optional[int],
    seeded_tasks: List[PlanNode],
    decomposition: Optional[DecompositionResult],
    decomposition_status: str,
    collected_materials: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "decomposition_status": decomposition_status,
        "generated_at": _utc_now_iso(),
        "root_task_id": root_task_id,
        "seed_task_count": len(seeded_tasks),
        "node_count": plan_tree.node_count(),
        "material_collection": {
            "used": bool(collected_materials),
            "entries": collected_materials,
        },
        "decomposition": {
            "created_task_count": len(decomposition.created_tasks) if decomposition else 0,
            "processed_nodes": list(decomposition.processed_nodes) if decomposition else [],
            "failed_nodes": list(decomposition.failed_nodes) if decomposition else [],
            "stopped_reason": decomposition.stopped_reason if decomposition else None,
            "stats": dict(decomposition.stats) if decomposition else {},
        },
    }


def _merge_plan_generation_metadata(
    tree: PlanTree,
    *,
    root_task_id: Optional[int],
    seeded_tasks: List[PlanNode],
    decomposition: Optional[DecompositionResult],
    decomposition_status: str,
    collected_materials: List[Dict[str, Any]],
) -> Dict[str, Any]:
    metadata = dict(tree.metadata or {})
    metadata["plan_generation"] = _build_generation_metadata(
        plan_tree=tree,
        root_task_id=root_task_id,
        seeded_tasks=seeded_tasks,
        decomposition=decomposition,
        decomposition_status=decomposition_status,
        collected_materials=collected_materials,
    )
    return metadata


def _plan_generation_status(tree: PlanTree) -> str:
    metadata = tree.metadata if isinstance(tree.metadata, dict) else {}
    plan_generation = metadata.get("plan_generation") if isinstance(metadata.get("plan_generation"), dict) else {}
    return str(
        plan_generation.get("decomposition_status") or plan_generation.get("status") or ""
    ).strip().lower()


async def create_plan_and_generate(
    *,
    title: str,
    description: Optional[str],
    tasks: Optional[List[Dict[str, Any]]],
    owner: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    repo: Optional[PlanRepository] = None,
    decomposer: Optional[PlanDecomposer] = None,
    session_context: Optional[Dict[str, Any]] = None,
) -> PlanGenerationOutcome:
    repo = repo or PlanRepository()
    effective_metadata = dict(metadata or {})
    plan_tree = repo.create_plan(
        title=title,
        owner=owner,
        description=description,
        metadata=effective_metadata,
    )
    plan_id = plan_tree.id

    root_node = repo.create_task(
        plan_id,
        name=title,
        status="pending",
        instruction=_root_instruction(title, description),
        parent_id=None,
        metadata={"is_root": True, "task_type": "root"},
    )
    seeded_tasks = _create_seed_tasks(
        repo,
        plan_id,
        root_task_id=root_node.id,
        tasks=tasks,
    )

    collected_materials: List[Dict[str, Any]] = []
    effective_context = dict(session_context or {})
    if decomposer is None:
        decomposer = PlanDecomposer(repo=repo)

    effective_context, collected_materials = await collect_plan_generation_materials(
        title=title,
        description=description,
        tasks=tasks,
        session_context=effective_context,
        decomposer=decomposer,
    )

    decomposition: Optional[DecompositionResult] = None
    decomposition_status = "skipped"
    if decomposer.settings.model is not None:
        decomposition = await asyncio.to_thread(
            decomposer.run_plan,
            plan_id,
            max_depth=decomposer.settings.max_depth,
            node_budget=decomposer.settings.total_node_budget,
            session_context=effective_context,
        )
        decomposition_status = "completed" if not decomposition.failed_nodes else "partial"
    else:
        decomposition_status = "not_configured"

    updated_tree = repo.get_plan_tree(plan_id)
    merged_metadata = _merge_plan_generation_metadata(
        updated_tree,
        root_task_id=root_node.id,
        seeded_tasks=seeded_tasks,
        decomposition=decomposition,
        decomposition_status=decomposition_status,
        collected_materials=collected_materials,
    )
    repo.update_plan_metadata(plan_id, merged_metadata)
    updated_tree.metadata = merged_metadata

    return PlanGenerationOutcome(
        plan_tree=updated_tree,
        root_task_id=root_node.id,
        seeded_tasks=seeded_tasks,
        decomposition=decomposition,
        collected_materials=collected_materials,
        session_context=effective_context,
        decomposition_status=decomposition_status,
        auto_completed_generation=True,
    )


async def ensure_plan_generation_ready(
    *,
    plan_id: int,
    repo: Optional[PlanRepository] = None,
    decomposer: Optional[PlanDecomposer] = None,
    session_context: Optional[Dict[str, Any]] = None,
) -> PlanGenerationOutcome:
    repo = repo or PlanRepository()
    if decomposer is None:
        decomposer = PlanDecomposer(repo=repo)

    tree = repo.get_plan_tree(plan_id)
    status = _plan_generation_status(tree)
    if status == "completed":
        return PlanGenerationOutcome(
            plan_tree=tree,
            root_task_id=None,
            seeded_tasks=[],
            decomposition=None,
            collected_materials=[],
            session_context=session_context,
            decomposition_status=status,
            auto_completed_generation=False,
        )

    if decomposer.settings.model is None:
        return PlanGenerationOutcome(
            plan_tree=tree,
            root_task_id=None,
            seeded_tasks=[],
            decomposition=None,
            collected_materials=[],
            session_context=session_context,
            decomposition_status="not_configured",
            auto_completed_generation=False,
        )

    effective_context, collected_materials = await collect_plan_generation_materials(
        title=tree.title,
        description=tree.description,
        tasks=None,
        session_context=session_context,
        decomposer=decomposer,
    )
    decomposition = await asyncio.to_thread(
        decomposer.run_plan,
        plan_id,
        max_depth=decomposer.settings.max_depth,
        node_budget=decomposer.settings.total_node_budget,
        session_context=effective_context,
    )
    decomposition_status = "completed" if not decomposition.failed_nodes else "partial"

    updated_tree = repo.get_plan_tree(plan_id)
    merged_metadata = _merge_plan_generation_metadata(
        updated_tree,
        root_task_id=None,
        seeded_tasks=[],
        decomposition=decomposition,
        decomposition_status=decomposition_status,
        collected_materials=collected_materials,
    )
    repo.update_plan_metadata(plan_id, merged_metadata)
    updated_tree.metadata = merged_metadata

    return PlanGenerationOutcome(
        plan_tree=updated_tree,
        root_task_id=None,
        seeded_tasks=[],
        decomposition=decomposition,
        collected_materials=collected_materials,
        session_context=effective_context,
        decomposition_status=decomposition_status,
        auto_completed_generation=True,
    )