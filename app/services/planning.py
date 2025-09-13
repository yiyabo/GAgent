import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..interfaces import LLMProvider, TaskRepository
from ..llm import get_default_client
from ..repository.tasks import default_repo
from ..utils import parse_json_obj


async def BFS_planner_stream(
    goal: str,
    repo: Optional[TaskRepository] = None,
    client: Optional[LLMProvider] = None,
    max_depth: int = 10,
) -> AsyncGenerator[str, None]:
    """
    流式版本的 BFS 任务规划器：
    在分解任务并保存子任务后，向前端发送实时进度
    """
    repo = repo or default_repo
    client = client or get_default_client()

    def _sanitize_type(v: str) -> str:
        """规范化任务类型"""
        return v if v in ("root", "composite", "atomic") else "composite"

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
            print(
                f"[BFS_planner] Created task '{node['name']}' with ID {task_id} in database"
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
                        print(
                            f"[BFS_planner] Set parent relationship for task {task_id}"
                        )
                    except Exception as e:
                        print(f"[BFS_planner] WARNING: Failed to set parent: {e}")
                elif hasattr(repo, "update_task"):
                    try:
                        repo.update_task(task_id, parent_id=node["parent_db_id"])
                        print(
                            f"[BFS_planner] Updated parent relationship for task {task_id}"
                        )
                    except Exception as e:
                        print(f"[BFS_planner] WARNING: Failed to update parent: {e}")

        # 设置任务输入
        try:
            repo.upsert_task_input(task_id, node.get("prompt", ""))
        except Exception as e:
            print(f"[BFS_planner] WARNING: Failed to set input for task {task_id}: {e}")

        # 将任务链接到计划
        try:
            repo.link_task_to_plan(plan_id, task_id, "task")
        except Exception as e:
            print(f"[BFS_planner] WARNING: Failed to link task {task_id} to plan: {e}")

        # 生成任务上下文
        try:
            generate_task_context(repo, client, task_id, node)
        except Exception as e:
            print(
                f"[BFS_planner] WARNING: Failed to generate context for task {task_id}: {e}"
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
        # 发送初始化消息
        init_msg = {
            "stage": "initialization",
            "message": "开始分析目标...",
            "goal": goal[:100],
        }
        yield f"data: {json.dumps(init_msg)}\n\n"
        await asyncio.sleep(0.1)

        # Step 0: 创建计划
        print(f"[BFS_planner] Starting analysis for goal: {goal[:100]}...")

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
        print(f"[BFS_planner] Created plan '{title}' with ID {plan_id}")

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
1. Proper task types: "root", "composite", or "atomic"
2. Clear names and actionable prompts  
3. Complete coverage of the goal
4. Logical grouping and sequencing

Return JSON ONLY (no comments):
{{"title": "Plan Title", "tasks": [{{"name": "task name", "prompt": "detailed description", "task_type": "root"}}]}}"""

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

        # 初始化数据结构
        roots: List[Dict[str, Any]] = []
        flat_tree: List[Dict[str, Any]] = []
        bfs_order: List[str] = []
        evaluation_log: List[Dict[str, Any]] = []
        task_id_map: Dict[str, int] = {}

        serial = 0

        # 创建根任务节点并立即写入数据库
        for idx, t in enumerate(tasks0):
            n = _make_node(
                name=t.get("name", f"Task {idx + 1}"),
                prompt=t.get("prompt", ""),
                task_type=t.get("task_type", "root"),
                layer=0,
                parent_id=None,
                nid=f"L0_T{serial}",
                parent_db_id=None,
            )

            # 立即在数据库中创建任务
            db_task_id = _create_task_in_db(n, plan_id)
            n["db_id"] = db_task_id
            task_id_map[n["id"]] = db_task_id

            roots.append(n)
            flat_tree.append(n)
            bfs_order.append(n["id"])
            serial += 1

            # 发送根任务创建消息
            root_task_with_children = {**n, "children": []}
            yield f"data: {json.dumps(root_task_with_children)}\n\n"
            await asyncio.sleep(0.1)

        # Step 2: BFS traversal using a queue
        from collections import deque

        queue = deque(roots)

        processed_tasks = 0
        SAFETY_LIMIT = 300

        while queue and processed_tasks < SAFETY_LIMIT:
            parent = queue.popleft()
            processed_tasks += 1

            # Only composite/root tasks need evaluation
            if parent.get("task_type") not in ("root", "composite"):
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
                    continue

                evaluated_type = decision_obj.get("evaluated_task_type")
                subtasks = decision_obj.get("tasks", [])

                if evaluated_type == "composite" and subtasks:
                    parent["task_type"] = "composite"

                    for st in subtasks:
                        child = _make_node(
                            name=st.get("name", "Subtask"),
                            prompt=st.get("prompt", ""),
                            task_type=st.get("task_type", "atomic"),
                            layer=parent["layer"] + 1,
                            parent_id=parent["id"],
                            nid=f"L{parent['layer'] + 1}_T{serial}",
                            parent_db_id=parent["db_id"],
                        )
                        serial += 1

                        db_task_id = _create_task_in_db(child, plan_id)
                        child["db_id"] = db_task_id
                        task_id_map[child["id"]] = db_task_id

                        parent["children"].append(child)
                        flat_tree.append(child)
                        queue.append(
                            child
                        )  # Add new subtask to the queue for processing

                        # Stream the new subtask to the client
                        subtask_with_children = {**child, "children": []}
                        yield f"data: {json.dumps(subtask_with_children)}\n\n"
                        await asyncio.sleep(0.1)

                else:  # Atomic or composite with no subtasks
                    parent["task_type"] = "atomic"

            except Exception as e:
                parent["task_type"] = "atomic"
                error_msg = {
                    "stage": "task_error",
                    "task_name": parent["name"],
                    "error": str(e),
                }
                # yield f"data: {json.dumps(error_msg)}\n\n" # Temporarily disable verbose events

        # Final completion event (optional, but good practice)
        completed_msg = {
            "event": "completion",
            "message": f"Plan generation complete. Total tasks: {len(flat_tree)}",
        }
        yield f"data: {json.dumps(completed_msg)}\n\n"

    except Exception as e:
        print(f"[BFS_planner] FATAL ERROR: {e}")
        error_result = {"success": False, "error": str(e)}
        error_msg = {
            "event": "error",
            "message": f"An error occurred: {str(e)}",
        }
        yield f"data: {json.dumps(error_msg)}\n\n"


def BFS_planner(
    goal: str,
    repo: Optional[TaskRepository] = None,
    client: Optional[LLMProvider] = None,
    max_depth: int = 10,
) -> Dict[str, Any]:
    """
    改进版 BFS 任务规划器（同步版本）
    """
    repo = repo or default_repo
    client = client or get_default_client()

    def _sanitize_type(v: str) -> str:
        """规范化任务类型"""
        return v if v in ("root", "composite", "atomic") else "composite"

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
            print(
                f"[BFS_planner] Created task '{node['name']}' with ID {task_id} in database"
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
                        print(
                            f"[BFS_planner] Set parent relationship for task {task_id}"
                        )
                    except Exception as e:
                        print(f"[BFS_planner] WARNING: Failed to set parent: {e}")
                elif hasattr(repo, "update_task"):
                    try:
                        repo.update_task(task_id, parent_id=node["parent_db_id"])
                        print(
                            f"[BFS_planner] Updated parent relationship for task {task_id}"
                        )
                    except Exception as e:
                        print(f"[BFS_planner] WARNING: Failed to update parent: {e}")

        # 设置任务输入
        try:
            repo.upsert_task_input(task_id, node.get("prompt", ""))
        except Exception as e:
            print(f"[BFS_planner] WARNING: Failed to set input for task {task_id}: {e}")

        # 将任务链接到计划
        try:
            repo.link_task_to_plan(plan_id, task_id, "task")
        except Exception as e:
            print(f"[BFS_planner] WARNING: Failed to link task {task_id} to plan: {e}")

        # 生成任务上下文
        try:
            generate_task_context(repo, client, task_id, node)
        except Exception as e:
            print(
                f"[BFS_planner] WARNING: Failed to generate context for task {task_id}: {e}"
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
        print(f"[BFS_planner] Starting analysis for goal: {goal[:100]}...")

        title_prompt = f"""Generate a concise, descriptive title for this goal:

Goal: {goal}

Return only the title (no quotes, no extra text):"""

        try:
            title_response = client.chat(title_prompt)
            title = title_response.strip()[:100] or goal[:60]
        except Exception:
            title = goal[:60]

        plan_id = repo.create_plan(title, description=goal)
        print(f"[BFS_planner] Created plan '{title}' with ID {plan_id}")

        # 生成第一层任务
        root_prompt = f"""Analyze this goal and create the optimal root-level tasks (Layer 0):

Goal: {goal}

Create the initial layer with:
1. Proper task types: "root", "composite", or "atomic"
2. Clear names and actionable prompts  
3. Complete coverage of the goal
4. Logical grouping and sequencing

Return JSON ONLY (no comments):
{{"title": "Plan Title", "tasks": [{{"name": "task name", "prompt": "detailed description", "task_type": "root"}}]}}"""

        root_content = client.chat(root_prompt)
        root_obj = parse_json_obj(root_content)

        if not root_obj:
            print("[BFS_planner] ERROR: Failed to parse initial planning response")
            return {
                "success": False,
                "error": "Failed to parse initial planning response",
            }

        tasks0 = root_obj.get("tasks", [])
        if not tasks0:
            print("[BFS_planner] ERROR: No initial tasks generated")
            return {"success": False, "error": "Failed to generate initial tasks"}

        print(f"[BFS_planner] Generated {len(tasks0)} root tasks for plan: {title}")

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
                task_type=t.get("task_type", "root"),
                layer=0,
                parent_id=None,
                nid=f"L0_T{serial}",
                parent_db_id=None,
            )

            db_task_id = _create_task_in_db(n, plan_id)
            n["db_id"] = db_task_id
            task_id_map[n["id"]] = db_task_id

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
            print(
                f"[BFS_planner] Processing Layer {current_layer} with {len(current_layer_nodes)} tasks"
            )
            next_layer_nodes: List[Dict[str, Any]] = []

            for parent in current_layer_nodes:
                if parent.get("task_type") not in ("root", "composite"):
                    print(f"[BFS_planner] Skipping atomic task: {parent['name']}")
                    continue

                print(f"[BFS_planner] Evaluating: {parent['name']}")

                task_prompt = _build_improved_assessment_prompt(parent, current_layer)

                try:
                    decision_raw = client.chat(task_prompt)
                    decision_obj = parse_json_obj(decision_raw)

                    if not decision_obj:
                        print(
                            f"[BFS_planner] WARNING: Failed to parse AI response for '{parent['name']}', treating as atomic"
                        )
                        evaluation_log.append({
                            "task_name": parent["name"],
                            "layer": current_layer,
                            "status": "parse_failed",
                            "action": "marked_atomic",
                        })
                        parent["task_type"] = "atomic"
                        continue

                    evaluated_type = decision_obj.get("evaluated_task_type")
                    subtasks = decision_obj.get("tasks", [])
                    reasoning = decision_obj.get("reasoning", "")

                    log_entry = {
                        "task_name": parent["name"],
                        "layer": current_layer,
                        "evaluated_type": evaluated_type,
                        "reasoning": reasoning,
                        "subtask_count": len(subtasks),
                    }

                    if evaluated_type == "composite":
                        if subtasks:
                            print(
                                f"[BFS_planner] Decomposing '{parent['name']}' into {len(subtasks)} subtasks"
                            )
                            parent["decomposed"] = True
                            parent["task_type"] = "composite"

                            for st in subtasks:
                                child = _make_node(
                                    name=st.get("name", "Subtask"),
                                    prompt=st.get(
                                        "prompt",
                                        f"Complete {st.get('name', 'subtask')}",
                                    ),
                                    task_type=st.get("task_type", "atomic"),
                                    layer=parent["layer"] + 1,
                                    parent_id=parent["id"],
                                    nid=f"{parent['id']}_S{serial}",
                                    parent_db_id=parent["db_id"],
                                )

                                db_task_id = _create_task_in_db(child, plan_id)
                                child["db_id"] = db_task_id
                                task_id_map[child["id"]] = db_task_id

                                parent["children"].append(child)
                                next_layer_nodes.append(child)
                                flat_tree.append(child)
                                bfs_order.append(child["id"])
                                serial += 1

                            log_entry["status"] = "decomposed"
                            log_entry["action"] = f"created_{len(subtasks)}_subtasks"
                        else:
                            print(
                                f"[BFS_planner] WARNING: '{parent['name']}' classified as composite but no subtasks provided"
                            )
                            parent["task_type"] = "composite"
                            log_entry["status"] = "composite_no_subtasks"
                            log_entry["action"] = "kept_composite"

                    elif evaluated_type == "atomic":
                        print(f"[BFS_planner] Finalizing '{parent['name']}' as atomic")
                        parent["task_type"] = "atomic"
                        log_entry["status"] = "atomic"
                        log_entry["action"] = "marked_atomic"

                    else:
                        print(
                            f"[BFS_planner] WARNING: Unknown evaluation type '{evaluated_type}' for '{parent['name']}', treating as atomic"
                        )
                        parent["task_type"] = "atomic"
                        log_entry["status"] = "unknown_type"
                        log_entry["action"] = "marked_atomic_fallback"

                    evaluation_log.append(log_entry)

                except Exception as e:
                    print(
                        f"[BFS_planner] ERROR: Exception during evaluation of '{parent['name']}': {e}"
                    )
                    parent["task_type"] = "atomic"
                    evaluation_log.append({
                        "task_name": parent["name"],
                        "layer": current_layer,
                        "status": "error",
                        "error": str(e),
                        "action": "marked_atomic_error",
                    })

                if len(flat_tree) > SAFETY_LIMIT:
                    print(
                        f"[BFS_planner] WARNING: Hit safety limit of {SAFETY_LIMIT} tasks"
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

        print(
            f"[BFS_planner] Completed: {len(flat_tree)} tasks across {max(layer_distribution.keys(), default=0) + 1} layers"
        )
        print(f"[BFS_planner] Task types: {type_distribution}")
        print(f"[BFS_planner] Stopped due to: {stopped_reason}")

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
        print(f"[BFS_planner] FATAL ERROR: {e}")
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
                        print(
                            f"WARNING: Failed to set parent_id for approved task {idx}"
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
