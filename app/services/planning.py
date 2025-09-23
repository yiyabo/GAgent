import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..interfaces import LLMProvider, TaskRepository
from ..llm import get_default_client
from ..repository.tasks import default_repo
from ..utils import parse_json_obj
from .plan_session import plan_session_manager

# Tracks in-progress plan generations so they can be cancelled externally.
_planner_cancellation_registry: Dict[int, asyncio.Event] = {}

logger = logging.getLogger(__name__)


def request_planner_cancellation(plan_id: int) -> bool:
    """Signal a running planner stream to cancel. Returns True if signalled."""

    event = _planner_cancellation_registry.get(plan_id)
    if event:
        event.set()
        return True
    return False


def _build_fallback_subtasks(task_name: str) -> List[Dict[str, str]]:
    """Create a simple three-phase fallback decomposition for a task."""

    base = (task_name or "Task").strip()
    if ":" in base:
        base_prefix = base.split(":", 1)[0].strip() or base
    else:
        base_prefix = base

    phases = [
        ("Analysis", f"Analyze requirements, constraints, and resources for {base} to clarify the work."),
        ("Execution", f"Carry out the main activities required to fulfill {base} and produce tangible outputs."),
        ("Validation", f"Review results, verify success criteria, and capture learnings for {base}."),
    ]

    return [
        {
            "name": f"{base_prefix} - {phase}",
            "prompt": prompt,
            "task_type": "atomic",
        }
        for phase, prompt in phases
    ]


async def BFS_planner_stream(
    goal: str,
    repo: Optional[TaskRepository] = None,
    client: Optional[LLMProvider] = None,
    max_depth: int = 3,
) -> AsyncGenerator[str, None]:
    """
    流式版本的 BFS 任务规划器：
    在分解任务并保存子任务后，向前端发送实时进度
    """
    repo = repo or default_repo
    client = client or get_default_client()

    def _sanitize_type(v: Optional[str]) -> str:
        """Normalize task type into 'composite' or 'atomic'."""
        if v is None:
            return "composite"
        normalized = str(v).strip().lower()
        if normalized == "root":
            return "composite"
        return normalized if normalized in {"composite", "atomic"} else "composite"

    def _create_task_node(
        plan_session,
        name: str,
        prompt: str,
        task_type: str,
        layer: int,
        parent_temp_id: Optional[int],
    ) -> Dict[str, Any]:
        info = plan_session.create_task(
            name=name,
            parent_id=parent_temp_id,
            task_type=task_type,
            priority=layer * 10,
        )
        temp_id = info["id"]
        if prompt:
            plan_session.set_instruction(temp_id, prompt)

        node = {
            "temp_id": temp_id,
            "id": temp_id,
            "name": info.get("name", name),
            "prompt": prompt,
            "task_type": info.get("task_type", task_type),
            "layer": layer,
            "parent_temp_id": parent_temp_id,
            "parent_id": parent_temp_id,
            "children": [],
        }
        return node

    def _build_improved_assessment_prompt(task: Dict[str, Any], depth: int) -> str:
        """改进的任务评估提示"""
        name = task.get("name", "")
        prompt = task.get("prompt", "")
        current_type = task.get("task_type", "composite")

        return f"""You are an expert project planner re-evaluating ONE task at Layer {depth}.

Task to Evaluate:
- Name: {name}
- Description: {prompt}
- Current Assumed Type: {current_type}

Your job is to determine the task's TRUE type:
- If the task is complex, multi-step, or too broad, its type is 'composite'
- If the task is a single, clear, and immediately actionable step, its type is 'atomic'

Rules:
1. If you classify it as 'composite', you MUST decompose it into immediate subtasks
2. If you classify it as 'atomic', the 'tasks' array MUST be empty
3. Subtasks should be concrete, actionable, and non-overlapping
4. Each subtask should have a clear name and detailed prompt

Return EXACT JSON (no comments):
{{
  "task_name": "{name}",
  "evaluated_task_type": "composite",
  "reasoning": "brief explanation of why this classification was chosen",
  "tasks": [
    {{"name": "subtask name", "prompt": "clear, actionable description", "task_type": "atomic"}}
  ]
}}

OR for atomic tasks:
{{
  "task_name": "{name}",
  "evaluated_task_type": "atomic",
  "reasoning": "brief explanation of why this is atomic",
  "tasks": []
}}"""

    plan_id: Optional[int] = None
    cancel_event: Optional[asyncio.Event] = None
    plan_session = None

    try:
        # 发送初始化消息
        init_msg = {
            "stage": "initialization",
            "message": "开始分析目标...",
            "goal": goal[:100],
        }
        yield f"data: {json.dumps(init_msg)}\n\n"
        await asyncio.sleep(0.1)

        # Step 0: 创建计划
        logger.info("[planner] Starting stream generation: %.100s", goal)

        # 生成计划标题
        title_prompt = f"""Generate a concise, descriptive title for this goal:

Goal: {goal}

Return only the title (no quotes, no extra text):"""

        try:
            title_response = client.chat(title_prompt)
            title = title_response.strip()[:100] or goal[:60]
        except Exception:
            title = goal[:60]

        # 在数据库中创建计划
        plan_id = repo.create_plan(title, description=goal)
        logger.info("[planner] Created plan '%s' (id=%s)", title, plan_id)

        cancel_event = asyncio.Event()
        _planner_cancellation_registry[plan_id] = cancel_event

        # Initialize plan session for in-memory graph management
        plan_session_manager.flush_stale()
        plan_session = plan_session_manager.activate_plan(plan_id)
        tasks_for_context: List[Dict[str, Any]] = []

        # 发送计划创建成功消息
        plan_msg = {
            "stage": "plan_created",
            "message": f"创建计划: {title}",
            "plan_id": plan_id,
            "title": title,
        }
        yield f"data: {json.dumps(plan_msg)}\n\n"
        await asyncio.sleep(0.1)

        # Step 1: 生成第一层任务
        root_msg = {"stage": "generating_root_tasks", "message": "正在生成根级任务..."}
        yield f"data: {json.dumps(root_msg)}\n\n"
        await asyncio.sleep(0.1)

        root_prompt = f"""Analyze this goal and create the optimal root-level tasks (Layer 0):

Goal: {goal}

Create the initial layer with:
1. Proper task types: "composite" or "atomic"
2. Clear names and actionable prompts  
3. Complete coverage of the goal
4. Logical grouping and sequencing

Return JSON ONLY (no comments):
{{"title": "Plan Title", "tasks": [{{"name": "task name", "prompt": "detailed description", "task_type": "composite"}}]}}"""

        root_content = client.chat(root_prompt)
        root_obj = parse_json_obj(root_content)

        if not root_obj:
            error_msg = {
                "stage": "error",
                "message": "Failed to parse initial planning response",
            }
            yield f"data: {json.dumps(error_msg)}\n\n"
            return

        tasks0 = root_obj.get("tasks", [])
        if not tasks0:
            error_msg = {
                "stage": "error",
                "message": "Failed to generate initial tasks",
            }
            yield f"data: {json.dumps(error_msg)}\n\n"
            return

        # 发送根任务生成成功消息
        root_success_msg = {
            "stage": "root_tasks_generated",
            "message": f"生成了 {len(tasks0)} 个根级任务",
            "task_count": len(tasks0),
        }
        yield f"data: {json.dumps(root_success_msg)}\n\n"
        await asyncio.sleep(0.1)

        def build_cancel_message() -> Optional[Dict[str, Any]]:
            if cancel_event and cancel_event.is_set():
                return {
                    "stage": "cancelled",
                    "plan_id": plan_id,
                    "title": title,
                    "message": "Plan generation cancelled by user.",
                }
            return None

        # 初始化数据结构
        roots: List[Dict[str, Any]] = []
        flat_tree: List[Dict[str, Any]] = []
        bfs_order: List[int] = []
        evaluation_log: List[Dict[str, Any]] = []

        # 创建根任务，先写入会话缓存
        for idx, t in enumerate(tasks0):
            name = t.get("name", f"Task {idx + 1}")
            prompt = t.get("prompt", "")
            task_type = _sanitize_type(t.get("task_type", "root"))

            node = _create_task_node(
                plan_session=plan_session,
                name=name,
                prompt=prompt,
                task_type=task_type,
                layer=0,
                parent_temp_id=None,
            )
            logger.info(
                "[planner] Root task created (stream): id=%s name=%s type=%s",
                node["temp_id"],
                node["name"],
                node["task_type"],
            )

            tasks_for_context.append({
                "temp_id": node["temp_id"],
                "name": node["name"],
                "prompt": node["prompt"],
                "task_type": node["task_type"],
                "layer": node["layer"],
                "parent_temp_id": node.get("parent_temp_id"),
            })

            roots.append(node)
            flat_tree.append(node)
            bfs_order.append(node["temp_id"])

            root_event = {
                "stage": "root_task_created",
                "task_id": node["temp_id"],
                "task_name": node["name"],
                "task_type": node["task_type"],
                "parent_id": None,
                "id": node["temp_id"],
                "temp_id": node["temp_id"],
                "name": node["name"],
                "layer": node["layer"],
                "prompt": node["prompt"],
            }
            yield f"data: {json.dumps(root_event)}\n\n"
            await asyncio.sleep(0.1)

            cancel_payload = build_cancel_message()
            if cancel_payload:
                yield f"data: {json.dumps(cancel_payload)}\n\n"
                return

        # Step 2: BFS traversal using a queue
        from collections import deque

        queue = deque(roots)

        processed_tasks = 0
        SAFETY_LIMIT = 300

        while queue and processed_tasks < SAFETY_LIMIT:
            cancel_payload = build_cancel_message()
            if cancel_payload:
                yield f"data: {json.dumps(cancel_payload)}\n\n"
                return

            parent = queue.popleft()
            processed_tasks += 1

            # Enforce depth cap to avoid infinite decomposition while keeping
            # explicit task types intact.
            node_layer = parent.get("layer", 0)
            if node_layer >= max_depth:
                continue

            if node_layer >= max_depth - 1 and parent.get("task_type") == "atomic":
                logger.debug(
                    "[planner] Reached terminal depth for atomic task: id=%s name=%s layer=%s",
                    parent.get("temp_id"),
                    parent.get("name"),
                    node_layer,
                )
                continue

            # Emit evaluation event
            eval_msg = {
                "stage": "evaluating_task",
                "message": f"Evaluating task: {parent['name']}",
                "task_name": parent["name"],
                "layer": parent["layer"],
            }
            # yield f"data: {json.dumps(eval_msg)}\n\n" # Temporarily disable verbose events
            await asyncio.sleep(0.1)

            task_prompt = _build_improved_assessment_prompt(parent, parent["layer"])

            try:
                decision_raw = client.chat(task_prompt)
                decision_obj = parse_json_obj(decision_raw)

                if not decision_obj:
                    parent["task_type"] = "atomic"
                    parent["llm_task_type"] = "atomic"
                    logger.info(
                        "[planner] Task marked atomic (no LLM response): id=%s name=%s",
                        parent["temp_id"],
                        parent["name"],
                    )
                    continue

                evaluated_type = _sanitize_type(decision_obj.get("evaluated_task_type"))
                subtasks = decision_obj.get("tasks", [])
                parent["llm_task_type_raw"] = evaluated_type
                parent["llm_task_type"] = evaluated_type

                fallback_used = False
                if (
                    evaluated_type == "composite"
                    and not subtasks
                    and node_layer < max_depth - 1
                ):
                    fallback_candidates = _build_fallback_subtasks(
                        parent.get("name", "Task")
                    )
                    if fallback_candidates:
                        logger.info(
                            "[planner] Fallback decomposition applied: id=%s name=%s layer=%s phases=%s",
                            parent.get("temp_id"),
                            parent.get("name"),
                            node_layer,
                            len(fallback_candidates),
                        )
                        subtasks = fallback_candidates
                        fallback_used = True
                        parent["fallback_decomposition"] = True

                if parent.get("task_type") != evaluated_type:
                    plan_session.update_task(parent["temp_id"], task_type=evaluated_type)
                    parent["task_type"] = evaluated_type

                if evaluated_type == "composite" and subtasks:
                    logger.info(
                        "[planner] Decomposing task: id=%s name=%s subtasks=%d",
                        parent["temp_id"],
                        parent["name"],
                        len(subtasks),
                    )

                    for st in subtasks:
                        child_name = st.get("name", "Subtask")
                        child_prompt = st.get("prompt", "")
                        child_type = _sanitize_type(st.get("task_type"))
                        child_layer = parent["layer"] + 1

                        if child_layer > max_depth:
                            logger.info(
                                "[planner] Skipping subtask beyond depth: parent=%s child=%s layer=%s",
                                parent["temp_id"],
                                child_name,
                                child_layer,
                            )
                            continue

                        child = _create_task_node(
                            plan_session=plan_session,
                            name=child_name,
                            prompt=child_prompt,
                            task_type=child_type,
                            layer=child_layer,
                            parent_temp_id=parent["temp_id"],
                        )
                        child["llm_task_type"] = child_type
                        logger.info(
                            "[planner] Subtask created (stream): id=%s parent=%s name=%s type=%s layer=%s",
                            child["temp_id"],
                            parent["temp_id"],
                            child["name"],
                            child["task_type"],
                            child_layer,
                        )
                        tasks_for_context.append({
                            "temp_id": child["temp_id"],
                            "name": child["name"],
                            "prompt": child["prompt"],
                            "task_type": child["task_type"],
                            "llm_task_type": child_type,
                            "layer": child["layer"],
                            "parent_temp_id": child.get("parent_temp_id"),
                        })

                        parent["children"].append(child)
                        flat_tree.append(child)
                        if child.get("task_type") == "composite" and child_layer < max_depth:
                            queue.append(child)
                        bfs_order.append(child["temp_id"])

                        subtask_event = {
                            "stage": "subtask_created",
                            "task_id": child["temp_id"],
                            "task_name": child["name"],
                            "task_type": child["task_type"],
                            "parent_id": parent["temp_id"],
                            "id": child["temp_id"],
                            "temp_id": child["temp_id"],
                            "name": child["name"],
                            "layer": child["layer"],
                            "prompt": child["prompt"],
                        }
                        yield f"data: {json.dumps(subtask_event)}\n\n"
                        await asyncio.sleep(0.1)

                        cancel_payload = build_cancel_message()
                        if cancel_payload:
                            yield f"data: {json.dumps(cancel_payload)}\n\n"
                            return

                else:
                    logger.info(
                        "[planner] Task finalized without further decomposition: id=%s name=%s type=%s",
                        parent["temp_id"],
                        parent["name"],
                        evaluated_type,
                    )
                    if node_layer < max_depth - 1:
                        logger.debug(
                            "[planner] No subtasks generated before depth limit: id=%s layer=%s llm_type=%s",
                            parent.get("temp_id"),
                            node_layer,
                            parent.get("llm_task_type"),
                        )

                evaluation_log.append(
                    {
                        "task_name": parent.get("name"),
                        "layer": node_layer,
                        "evaluated_type": evaluated_type,
                        "llm_type": parent.get("llm_task_type"),
                        "subtask_count": len(subtasks) if evaluated_type == "composite" and subtasks else 0,
                        "fallback_used": fallback_used,
                    }
                )

            except Exception as e:
                parent["task_type"] = "atomic"
                parent["llm_task_type"] = "atomic"
                error_msg = {
                    "stage": "task_error",
                    "task_name": parent["name"],
                    "error": str(e),
                }
                logger.exception(
                    "[planner] Exception while decomposing task (stream): id=%s name=%s",
                    parent["temp_id"],
                    parent["name"],
                )
                # yield f"data: {json.dumps(error_msg)}\n\n" # Temporarily disable verbose events

        # Persist tasks from session to database once generation completes
        cancel_payload = build_cancel_message()
        if cancel_payload:
            yield f"data: {json.dumps(cancel_payload)}\n\n"
            return

        id_map = plan_session.commit()
        task_id_map: Dict[int, int] = {}

        if id_map:
            for node in flat_tree:
                final_id = id_map.get(node["temp_id"], node["temp_id"])
                node["id"] = final_id
                node["parent_id"] = (
                    None
                    if node.get("parent_temp_id") is None
                    else id_map.get(node["parent_temp_id"], node["parent_temp_id"])
                )
                task_id_map[node["temp_id"]] = final_id
            bfs_order = [id_map.get(temp_id, temp_id) for temp_id in bfs_order]
        else:
            for node in flat_tree:
                task_id_map[node["temp_id"]] = node["id"]

        # Generate task contexts now that we have stable database IDs
        if task_id_map:
            for meta in tasks_for_context:
                temp_id = meta["temp_id"]
                actual_id = task_id_map.get(temp_id)
                if not actual_id:
                    continue
                try:
                    generate_task_context(
                        repo,
                        client,
                        actual_id,
                        {
                            "name": meta.get("name"),
                            "prompt": meta.get("prompt"),
                            "task_type": meta.get("task_type"),
                            "layer": meta.get("layer"),
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "[planner] Failed to generate context for task %s: %s",
                        actual_id,
                        exc,
                    )

        # Refresh plan session view to ensure we have persisted data
        persisted_tasks = plan_session.list_tasks()
        persisted_tree = plan_session.build_task_tree()

        # Statistics for analytics / completion event
        layer_distribution: Dict[int, int] = {}
        type_distribution: Dict[str, int] = {}

        for node in flat_tree:
            layer = node.get("layer", 0)
            task_type = node.get("task_type", "unknown")
            layer_distribution[layer] = layer_distribution.get(layer, 0) + 1
            type_distribution[task_type] = type_distribution.get(task_type, 0) + 1

        stopped_reason = (
            "safety_limit" if processed_tasks >= SAFETY_LIMIT else "completed"
        )

        completed_msg = {
            "stage": "completed",
            "plan_id": plan_id,
            "title": title,
            "result": {
                "flat_tree": flat_tree,
                "persisted_tasks": persisted_tasks,
                "task_tree": persisted_tree,
                "task_id_map": task_id_map,
                "total_tasks": len(flat_tree),
                "layer_distribution": layer_distribution,
                "type_distribution": type_distribution,
                "stopped_reason": stopped_reason,
                "bfs_order": bfs_order,
                "evaluation_log": evaluation_log,
            },
            "message": f"Plan generation complete. Total tasks: {len(flat_tree)}",
        }
        yield f"data: {json.dumps(completed_msg)}\n\n"

    except Exception as e:
        logger.exception("[planner] Stream planner fatal error")
        error_result = {"success": False, "error": str(e)}
        error_msg = {
            "stage": "fatal_error",
            "plan_id": plan_id,
            "message": f"An error occurred: {str(e)}",
        }
        yield f"data: {json.dumps(error_msg)}\n\n"
    finally:
        if plan_id is not None:
            _planner_cancellation_registry.pop(plan_id, None)
            if cancel_event and cancel_event.is_set() and plan_session:
                try:
                    plan_session.refresh()
                except Exception:
                    pass
            plan_session_manager.release_session(plan_id)


def BFS_planner(
    goal: str,
    repo: Optional[TaskRepository] = None,
    client: Optional[LLMProvider] = None,
    max_depth: int = 3,
) -> Dict[str, Any]:
    """
    改进版 BFS 任务规划器（同步版本）
    """
    repo = repo or default_repo
    client = client or get_default_client()

    def _sanitize_type(v: Optional[str]) -> str:
        if v is None:
            return "composite"
        normalized = str(v).strip().lower()
        if normalized == "root":
            return "composite"
        return normalized if normalized in {"composite", "atomic"} else "composite"

    def _make_node(
        name: str,
        prompt: str,
        task_type: str,
        layer: int,
        parent_id: Optional[str],
        nid: str,
        parent_db_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建任务节点"""
        return {
            "id": nid,
            "name": name,
            "prompt": prompt or "",
            "task_type": _sanitize_type(task_type),
            "layer": layer,
            "parent_id": parent_id,
            "parent_db_id": parent_db_id,
            "children": [],
            "db_id": None,
        }

    def _create_task_in_db(node: Dict[str, Any], plan_id: int) -> int:
        """在数据库中创建任务并返回任务ID"""
        try:
            task_id = repo.create_task(
                name=node["name"],
                status="pending",
                priority=node.get("layer", 0) * 10,
                task_type=node.get("task_type", "composite"),
                parent_id=node.get("parent_db_id"),
            )
            logger.info(
                "[planner] Created DB task: plan_id=%s task_id=%s name=%s type=%s layer=%s parent_db=%s",
                plan_id,
                task_id,
                node["name"],
                node.get("task_type"),
                node.get("layer"),
                node.get("parent_db_id"),
            )
        except TypeError:
            task_id = repo.create_task(
                name=node["name"],
                status="pending",
                priority=node.get("layer", 0) * 10,
            )

            if node.get("parent_db_id"):
                if hasattr(repo, "set_task_parent"):
                    try:
                        repo.set_task_parent(task_id, node["parent_db_id"])
                        logger.info(
                            "[planner] Set parent relationship: task_id=%s parent_db=%s",
                            task_id,
                            node["parent_db_id"],
                        )
                    except Exception as e:
                        logger.warning(
                            "[planner] Failed to set parent relationship: task_id=%s error=%s",
                            task_id,
                            e,
                        )
                elif hasattr(repo, "update_task"):
                    try:
                        repo.update_task(task_id, parent_id=node["parent_db_id"])
                        logger.info(
                            "[planner] Updated parent relationship: task_id=%s parent_db=%s",
                            task_id,
                            node["parent_db_id"],
                        )
                    except Exception as e:
                        logger.warning(
                            "[planner] Failed to update parent relationship: task_id=%s error=%s",
                            task_id,
                            e,
                        )

        # 设置任务输入
        try:
            repo.upsert_task_input(task_id, node.get("prompt", ""))
        except Exception as e:
            logger.warning(
                "[planner] Failed to upsert task input: task_id=%s error=%s",
                task_id,
                e,
            )

        # 将任务链接到计划
        try:
            repo.link_task_to_plan(plan_id, task_id, "task")
        except Exception as e:
            logger.warning(
                "[planner] Failed to link task to plan: plan_id=%s task_id=%s error=%s",
                plan_id,
                task_id,
                e,
            )

        # 生成任务上下文
        try:
            generate_task_context(repo, client, task_id, node)
        except Exception as e:
            logger.warning(
                "[planner] Failed to generate context for task %s: %s",
                task_id,
                e,
            )

        return task_id

    def _build_improved_assessment_prompt(task: Dict[str, Any], depth: int) -> str:
        """改进的任务评估提示"""
        name = task.get("name", "")
        prompt = task.get("prompt", "")
        current_type = task.get("task_type", "composite")

        return f"""You are an expert project planner re-evaluating ONE task at Layer {depth}.

Task to Evaluate:
- Name: {name}
- Description: {prompt}
- Current Assumed Type: {current_type}

Your job is to determine the task's TRUE type:
- If the task is complex, multi-step, or too broad, its type is 'composite'
- If the task is a single, clear, and immediately actionable step, its type is 'atomic'

Rules:
1. If you classify it as 'composite', you MUST decompose it into immediate subtasks
2. If you classify it as 'atomic', the 'tasks' array MUST be empty
3. Subtasks should be concrete, actionable, and non-overlapping
4. Each subtask should have a clear name and detailed prompt

Return EXACT JSON (no comments):
{{
  "task_name": "{name}",
  "evaluated_task_type": "composite",
  "reasoning": "brief explanation of why this classification was chosen",
  "tasks": [
    {{"name": "subtask name", "prompt": "clear, actionable description", "task_type": "atomic"}}
  ]
}}

OR for atomic tasks:
{{
  "task_name": "{name}",
  "evaluated_task_type": "atomic",
  "reasoning": "brief explanation of why this is atomic",
  "tasks": []
}}"""

    try:
        # 创建计划
        logger.info("[planner] Starting synchronous generation: %.100s", goal)

        title_prompt = f"""Generate a concise, descriptive title for this goal:

Goal: {goal}

Return only the title (no quotes, no extra text):"""

        try:
            title_response = client.chat(title_prompt)
            title = title_response.strip()[:100] or goal[:60]
        except Exception:
            title = goal[:60]

        plan_id = repo.create_plan(title, description=goal)
        logger.info("[planner] Created plan '%s' (id=%s)", title, plan_id)

        # 生成第一层任务
        root_prompt = f"""Analyze this goal and create the optimal root-level tasks (Layer 0):

Goal: {goal}

Create the initial layer with:
1. Proper task types: "composite" or "atomic"
2. Clear names and actionable prompts  
3. Complete coverage of the goal
4. Logical grouping and sequencing

Return JSON ONLY (no comments):
{{"title": "Plan Title", "tasks": [{{"name": "task name", "prompt": "detailed description", "task_type": "composite"}}]}}"""

        root_content = client.chat(root_prompt)
        root_obj = parse_json_obj(root_content)

        if not root_obj:
            logger.error("[planner] Failed to parse initial planning response")
            return {
                "success": False,
                "error": "Failed to parse initial planning response",
            }

        tasks0 = root_obj.get("tasks", [])
        if not tasks0:
            logger.error("[planner] No initial tasks generated")
            return {"success": False, "error": "Failed to generate initial tasks"}

        logger.info(
            "[planner] Generated %d root tasks for plan '%s'",
            len(tasks0),
            title,
        )

        # 初始化数据结构
        roots: List[Dict[str, Any]] = []
        flat_tree: List[Dict[str, Any]] = []
        bfs_order: List[str] = []
        evaluation_log: List[Dict[str, Any]] = []
        task_id_map: Dict[str, int] = {}

        serial = 0
        current_layer_nodes: List[Dict[str, Any]] = []

        # 创建根任务节点并立即写入数据库
        for idx, t in enumerate(tasks0):
            n = _make_node(
                name=t.get("name", f"Task {idx + 1}"),
                prompt=t.get("prompt", ""),
                task_type=_sanitize_type(t.get("task_type", "root")),
                layer=0,
                parent_id=None,
                nid=f"L0_T{serial}",
                parent_db_id=None,
            )

            db_task_id = _create_task_in_db(n, plan_id)
            n["db_id"] = db_task_id
            task_id_map[n["id"]] = db_task_id
            logger.info(
                "[planner] Root task persisted: plan_id=%s db_id=%s logical=%s name=%s type=%s",
                plan_id,
                db_task_id,
                n["id"],
                n["name"],
                n["task_type"],
            )

            roots.append(n)
            current_layer_nodes.append(n)
            flat_tree.append(n)
            bfs_order.append(n["id"])
            serial += 1

        # 层序遍历
        current_layer = 0
        SAFETY_LIMIT = 300

        while (
            current_layer_nodes
            and current_layer < max_depth
            and len(flat_tree) <= SAFETY_LIMIT
        ):
            logger.info(
                "[planner] Processing layer %s with %s tasks",
                current_layer,
                len(current_layer_nodes),
            )
            next_layer_nodes: List[Dict[str, Any]] = []

            for parent in current_layer_nodes:
                layer = parent.get("layer", 0)
                if layer >= max_depth:
                    continue

                if layer >= max_depth - 1 and parent.get("task_type") == "atomic":
                    logger.debug(
                        "[planner] Terminal depth reached for atomic task: id=%s name=%s layer=%s",
                        parent.get("id"),
                        parent.get("name"),
                        layer,
                    )
                    continue

                logger.info(
                    "[planner] Evaluating task: id=%s name=%s layer=%s",
                    parent["id"],
                    parent["name"],
                    parent.get("layer"),
                )

                task_prompt = _build_improved_assessment_prompt(parent, current_layer)

                try:
                    decision_raw = client.chat(task_prompt)
                    decision_obj = parse_json_obj(decision_raw)

                    if not decision_obj:
                        logger.warning(
                            "[planner] Failed to parse LLM response; marking atomic: id=%s name=%s",
                            parent["id"],
                            parent["name"],
                        )
                        evaluation_log.append({
                            "task_name": parent["name"],
                            "layer": current_layer,
                            "status": "parse_failed",
                            "action": "marked_atomic",
                        })
                        parent["task_type"] = "atomic"
                        parent["llm_task_type"] = "atomic"
                        continue

                    evaluated_type = _sanitize_type(
                        decision_obj.get("evaluated_task_type")
                    )
                    subtasks = decision_obj.get("tasks", [])
                    reasoning = decision_obj.get("reasoning", "")
                    parent["llm_task_type_raw"] = evaluated_type
                    parent["llm_task_type"] = evaluated_type

                    fallback_used = False
                    if (
                        evaluated_type == "composite"
                        and not subtasks
                        and layer < max_depth - 1
                    ):
                        fallback_candidates = _build_fallback_subtasks(
                            parent.get("name", "Task")
                        )
                        if fallback_candidates:
                            logger.info(
                                "[planner] Fallback decomposition applied: id=%s name=%s layer=%s phases=%s",
                                parent.get("id"),
                                parent.get("name"),
                                layer,
                                len(fallback_candidates),
                            )
                            subtasks = fallback_candidates
                            fallback_used = True
                            parent["fallback_decomposition"] = True

                    if parent.get("task_type") != evaluated_type:
                        parent["task_type"] = evaluated_type
                        db_id = parent.get("db_id")
                        if db_id:
                            try:
                                repo.update_task(db_id, task_type=evaluated_type)
                            except Exception as update_exc:
                                logger.warning(
                                    "[planner] Failed to update task type in DB: id=%s error=%s",
                                    db_id,
                                    update_exc,
                                )

                    log_entry = {
                        "task_name": parent["name"],
                        "layer": current_layer,
                        "evaluated_type": evaluated_type,
                        "reasoning": reasoning,
                        "subtask_count": len(subtasks),
                    }

                    if evaluated_type == "composite" and subtasks:
                        logger.info(
                            "[planner] Decomposing task: id=%s name=%s subtasks=%d",
                            parent["id"],
                            parent["name"],
                            len(subtasks),
                        )
                        parent["decomposed"] = True
                        parent["task_type"] = "composite"

                        for st in subtasks:
                            child_type = _sanitize_type(st.get("task_type"))
                            child_layer = parent["layer"] + 1

                            if child_layer > max_depth:
                                logger.info(
                                    "[planner] Skipping subtask beyond depth: parent=%s child=%s layer=%s",
                                    parent["id"],
                                    st.get("name", "Subtask"),
                                    child_layer,
                                )
                                continue

                            child = _make_node(
                                name=st.get("name", "Subtask"),
                                prompt=st.get(
                                    "prompt",
                                    f"Complete {st.get('name', 'subtask')}",
                                ),
                                task_type=child_type,
                                layer=child_layer,
                                parent_id=parent["id"],
                                nid=f"{parent['id']}_S{serial}",
                                parent_db_id=parent["db_id"],
                            )
                            child["llm_task_type"] = child_type

                            db_task_id = _create_task_in_db(child, plan_id)
                            child["db_id"] = db_task_id
                            task_id_map[child["id"]] = db_task_id
                            logger.info(
                                "[planner] Subtask persisted: plan_id=%s parent=%s db_id=%s logical=%s name=%s type=%s layer=%s",
                                plan_id,
                                parent["id"],
                                db_task_id,
                                child["id"],
                                child["name"],
                                child["task_type"],
                                child["layer"],
                            )

                            parent["children"].append(child)
                            if child_layer < max_depth:
                                next_layer_nodes.append(child)
                            flat_tree.append(child)
                            bfs_order.append(child["id"])
                            serial += 1

                        log_entry["status"] = "decomposed"
                        log_entry["action"] = (
                            "fallback_decomposition"
                            if fallback_used
                            else f"created_{len(subtasks)}_subtasks"
                        )
                        log_entry["fallback_used"] = fallback_used
                    else:
                        log_entry["status"] = evaluated_type
                        log_entry["action"] = "kept_type"
                        log_entry["fallback_used"] = False
                        logger.info(
                            "[planner] Task finalized without further decomposition: id=%s name=%s type=%s",
                            parent["id"],
                            parent["name"],
                            evaluated_type,
                        )

                    evaluation_log.append(log_entry)

                except Exception as e:
                    logger.exception(
                        "[planner] Exception evaluating task: id=%s name=%s",
                        parent["id"],
                        parent["name"],
                    )
                    parent["task_type"] = "atomic"
                    parent["llm_task_type"] = "atomic"
                    evaluation_log.append({
                        "task_name": parent["name"],
                        "layer": current_layer,
                        "status": "error",
                        "error": str(e),
                        "action": "marked_atomic_error",
                    })

                if len(flat_tree) > SAFETY_LIMIT:
                    logger.warning(
                        "[planner] Hit safety limit (%d tasks)", SAFETY_LIMIT
                    )
                    break

            if len(flat_tree) > SAFETY_LIMIT:
                break

            current_layer_nodes = next_layer_nodes
            current_layer += 1

        # 统计信息
        layer_distribution: Dict[int, int] = {}
        type_distribution: Dict[str, int] = {}

        for n in flat_tree:
            layer = n.get("layer", 0)
            task_type = n.get("task_type", "unknown")
            layer_distribution[layer] = layer_distribution.get(layer, 0) + 1
            type_distribution[task_type] = type_distribution.get(task_type, 0) + 1

        if current_layer >= max_depth:
            stopped_reason = "max_depth"
        elif len(flat_tree) > SAFETY_LIMIT:
            stopped_reason = "safety_limit"
        else:
            stopped_reason = "exhausted"

        logger.info(
            "[planner] Completed plan: plan_id=%s tasks=%d layers=%d stop=%s",
            plan_id,
            len(flat_tree),
            max(layer_distribution.keys(), default=0) + 1,
            stopped_reason,
        )
        logger.debug("[planner] Task type distribution: %s", type_distribution)

        return {
            "success": True,
            "goal": goal,
            "title": title,
            "plan_id": plan_id,
            "tree": roots,
            "flat_tree": flat_tree,
            "bfs_order": bfs_order,
            "total_tasks": len(flat_tree),
            "max_layer": max((n.get("layer", 0) for n in flat_tree), default=0),
            "layer_distribution": layer_distribution,
            "type_distribution": type_distribution,
            "evaluation_log": evaluation_log,
            "stopped_reason": stopped_reason,
            "task_id_map": task_id_map,
        }

    except Exception as e:
        logger.exception("[planner] Planner fatal error")
        return {"success": False, "error": str(e)}


def generate_task_context(
    repo: TaskRepository,
    client: LLMProvider,
    task_id: int,
    task_data: Dict[str, Any],
) -> None:
    """
    Generate initial task context with AI-determined metadata when a new task is created.
    """

    def _coerce_sections(val) -> List[Dict[str, Any]]:
        """把 sections 规整为 [{'title': str, 'content': str}, ...]"""
        out: List[Dict[str, Any]] = []
        if isinstance(val, list):
            for it in val:
                if isinstance(it, dict):
                    title = str(it.get("title", "section")).strip() or "section"
                    content = str(it.get("content", "")).strip()
                    out.append({"title": title, "content": content})
                else:
                    out.append({"title": "section", "content": str(it)})
        elif isinstance(val, dict):
            out.append({
                "title": str(val.get("title", "section")),
                "content": str(val.get("content", "")),
            })
        elif val is not None:
            out.append({"title": "section", "content": str(val)})
        return out

    def _coerce_meta(val) -> Dict[str, Any]:
        """确保 meta 为 dict，并补齐关键字段类型"""
        meta = val if isinstance(val, dict) else {}

        def _as_list(x):
            if x is None:
                return []
            if isinstance(x, list):
                return x
            return [x]

        meta.setdefault("complexity_level", "medium")
        meta["atomic_potential"] = bool(meta.get("atomic_potential", False))
        meta["technical_focus"] = _as_list(meta.get("technical_focus"))
        meta["domain_keywords"] = _as_list(meta.get("domain_keywords"))
        meta["risk_factors"] = _as_list(meta.get("risk_factors"))
        meta["dependency_hints"] = _as_list(meta.get("dependency_hints"))
        meta["skill_requirements"] = _as_list(meta.get("skill_requirements"))
        meta["tool_requirements"] = _as_list(meta.get("tool_requirements"))
        meta.setdefault("original_name", task_data.get("name"))
        meta.setdefault("task_type", task_data.get("task_type", "composite"))
        meta.setdefault("layer_depth", task_data.get("layer", 0))
        meta.setdefault("layer_id", task_data.get("layer", 0))
        meta.setdefault("created_context", "ai-generated")
        return meta

    try:
        logger.info(
            "[planner] Generating context for task_id=%s name=%s type=%s layer=%s",
            task_id,
            task_data.get("name"),
            task_data.get("task_type", "composite"),
            task_data.get("layer", 0),
        )
        context_prompt = f"""
Analyze this newly created task and generate comprehensive context including metadata.

Task Information:
- Name: {task_data["name"]}
- Description: {task_data["prompt"]}
- Type: {task_data.get("task_type", "composite")}
- Layer Depth: {task_data.get("layer", 0)}
- Parent Relationship: {"Root task" if task_data.get("layer", 0) == 0 else f"Child of task {task_data.get('parent_id', 'unknown')}"}

Generate context with the following structure:
1. combined: A comprehensive summary combining all insights
2. sections: Structured breakdown of different aspects
3. meta: Key metadata as JSON object with AI-determined key-value pairs

Return JSON ONLY:
{{
    "combined": "comprehensive task analysis summary",
    "sections": [
        {{"title": "aspect_name", "content": "detailed analysis of this aspect"}}
    ],
    "meta": {{
        "complexity_level": "low|medium|high",
        "atomic_potential": true|false,
        "technical_focus": [],
        "domain_keywords": [],
        "completion_criteria": "",
        "risk_factors": [],
        "dependency_hints": [],
        "skill_requirements": [],
        "tool_requirements": []
    }}
}}"""
        context_response = client.chat(context_prompt)
        context_result = parse_json_obj(context_response) or {}

        combined = context_result.get("combined")
        if not isinstance(combined, str) or not combined.strip():
            combined = f"Task: {task_data['name']}\n{task_data['prompt']}"

        sections = _coerce_sections(context_result.get("sections"))
        meta = _coerce_meta(context_result.get("meta"))

        repo.upsert_task_context(task_id, combined, sections, meta, label="ai-initial")
        logger.info(
            "[planner] Context stored for task_id=%s label=ai-initial sections=%s",
            task_id,
            len(sections),
        )

    except Exception as e:
        basic_context = {
            "combined": (
                f"Task: {task_data.get('name', '')}\n"
                f"Description: {task_data.get('prompt', '')}\n"
                f"Type: {task_data.get('task_type', 'composite')}\n"
                f"Layer: {task_data.get('layer', 0)}"
            ),
            "sections": [
                {
                    "title": "basic_info",
                    "content": (
                        f"Name: {task_data.get('name', '')}\n"
                        f"Task Type: {task_data.get('task_type', 'composite')}\n"
                        f"Parent: {('Root task' if task_data.get('layer', 0) == 0 else str(task_data.get('parent_id')))}"
                    ),
                }
            ],
            "meta": {
                "original_name": task_data.get("name"),
                "task_type": task_data.get("task_type", "composite"),
                "layer_depth": task_data.get("layer", 0),
                "created_context": "basic-fallback",
                "parent_id": task_data.get("parent_id"),
                "layer_id": task_data.get("layer", 0),
                "ai_context_generation_failed": str(e),
            },
        }
        try:
            repo.upsert_task_context(
                task_id,
                basic_context["combined"],
                basic_context["sections"],
                basic_context["meta"],
                label="basic-fallback",
            )
            logger.info(
                "[planner] Fallback context stored for task_id=%s label=basic-fallback",
                task_id,
            )
        except Exception:
            pass


def approve_plan_service(
    plan: Dict[str, Any], repo: Optional[TaskRepository] = None
) -> Dict[str, Any]:
    """
    批准计划服务
    """
    if not isinstance(plan, dict):
        raise ValueError("Body must be a JSON object")

    if plan.get("plan_id"):
        return {
            "success": True,
            "plan_id": plan["plan_id"],
            "message": "Plan already exists in database",
            "total_tasks": plan.get("total_tasks", 0),
            "max_layer": plan.get("max_layer", 0),
        }

    title = (plan.get("title") or "Untitled").strip()

    if plan.get("flat_tree"):
        tasks = plan["flat_tree"]
    else:
        tasks = plan.get("tree") or plan.get("tasks") or []
        if tasks and isinstance(tasks[0], dict) and "children" in tasks[0]:
            from collections import deque

            flat: List[Dict[str, Any]] = []
            q = deque(tasks)
            while q:
                n = q.popleft()
                flat.append(n)
                for c in n.get("children") or []:
                    q.append(c)
            tasks = flat

    if not tasks:
        raise ValueError("Plan has no tasks to approve")

    repo = repo or default_repo
    prefix = f"[{title}] "
    created: List[Dict[str, Any]] = []
    task_id_map: Dict[str, int] = {}

    plan_id = repo.create_plan(title, description=plan.get("goal", ""))

    bfs_order = plan.get("bfs_order") or []
    order_map = {tid: i for i, tid in enumerate(bfs_order)}

    tasks = sorted(
        tasks,
        key=lambda t: (t.get("layer", 0), order_map.get(t.get("id"), 10**9)),
    )

    for idx, task in enumerate(tasks):
        try:
            parent_id = (
                task_id_map.get(task.get("parent_id"))
                if task.get("parent_id")
                else None
            )
            priority = task.get("layer", 0) * 10 + idx

            try:
                task_id = repo.create_task(
                    name=f"{prefix}{task.get('name', f'Task_{idx}')}",
                    status="pending",
                    priority=priority,
                    task_type=task.get("task_type", "composite"),
                    parent_id=parent_id,
                )
            except TypeError:
                task_id = repo.create_task(
                    name=f"{prefix}{task.get('name', f'Task_{idx}')}",
                    status="pending",
                    priority=priority,
                )

                if parent_id and hasattr(repo, "set_task_parent"):
                    try:
                        repo.set_task_parent(task_id, parent_id)
                    except Exception:
                        logger.warning(
                            "[planner] Failed to set parent for task during approval: index=%s plan_id=%s",
                            idx,
                            plan_id,
                        )

            repo.upsert_task_input(task_id, task.get("prompt", ""))
            repo.link_task_to_plan(plan_id, task_id, "task")
            generate_task_context(repo, get_default_client(), task_id, task)

            task_id_map[task.get("id", idx)] = task_id
            created.append({
                "id": task_id,
                "name": task.get("name", f"Task_{idx}"),
                "priority": priority,
            })

        except Exception as e:
            return {"success": False, "error": str(e)}

    return {
        "success": True,
        "plan_id": plan_id,
        "plan": {"title": title},
        "created": created,
        "total_tasks": len(created),
        "max_layer": max((t.get("layer", 0) for t in tasks), default=0),
    }
