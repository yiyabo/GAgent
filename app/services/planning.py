from typing import Any, Dict, List, Optional

from ..interfaces import LLMProvider, TaskRepository
from ..llm import get_default_client
from ..repository.tasks import default_repo
from ..utils import parse_json_obj


def BFS_planner(
    goal: str,
    repo: Optional[TaskRepository] = None,
    client: Optional[LLMProvider] = None,
    max_depth: int = 10,
) -> Dict[str, Any]:
    """
    改进版 BFS 任务规划器：
    1) AI驱动的任务类型评估，而非简单的分解判断
    2) 健壮的错误处理和边界情况处理
    3) 详细的调试信息和执行日志
    4) 修复提示模板中的JSON格式问题
    5) 创建任务时就写入数据库，确保子任务能获得正确的parent_id
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
        parent_db_id: Optional[int] = None,  # 新增：数据库中的父任务ID
    ) -> Dict[str, Any]:
        """创建任务节点"""
        return {
            "id": nid,
            "name": name,
            "prompt": prompt or "",
            "task_type": _sanitize_type(task_type),
            "layer": layer,
            "parent_id": parent_id,
            "parent_db_id": parent_db_id,  # 新增：记录数据库中的父任务ID
            "children": [],
            "db_id": None,  # 新增：记录数据库中的任务ID
        }

    def _create_task_in_db(node: Dict[str, Any], plan_id: int) -> int:
        """在数据库中创建任务并返回任务ID"""
        try:
            # 尝试使用支持parent_id的create_task方法
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
            # 如果不支持新参数，使用兼容模式
            task_id = repo.create_task(
                name=node["name"],
                status="pending",
                priority=node.get("layer", 0) * 10,
            )

            # 尝试通过其他方式设置parent_id
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

            print(
                f"[BFS_planner] Created task '{node['name']}' with ID {task_id} (legacy mode)"
            )

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
        """改进的任务评估提示 - 修复JSON格式问题"""
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
        # Step 0: 首先创建计划
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

        # Step 1: 生成第一层（Layer 0）
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
        task_id_map: Dict[str, int] = {}  # 新增：映射节点ID到数据库ID

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

            # 立即在数据库中创建任务
            db_task_id = _create_task_in_db(n, plan_id)
            n["db_id"] = db_task_id
            task_id_map[n["id"]] = db_task_id

            roots.append(n)
            current_layer_nodes.append(n)
            flat_tree.append(n)
            bfs_order.append(n["id"])
            serial += 1

        # Step 2: 层序遍历 - 改进的AI评估逻辑
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
                # 只有 root/composite 才考虑评估
                if parent.get("task_type") not in ("root", "composite"):
                    print(f"[BFS_planner] Skipping atomic task: {parent['name']}")
                    continue

                print(f"[BFS_planner] Evaluating: {parent['name']}")

                # 使用改进的提示
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

                    # 记录评估结果
                    log_entry = {
                        "task_name": parent["name"],
                        "layer": current_layer,
                        "evaluated_type": evaluated_type,
                        "reasoning": reasoning,
                        "subtask_count": len(subtasks),
                    }

                    if evaluated_type == "composite":
                        if subtasks:
                            # 正常分解 - 立即创建子任务到数据库
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
                                    parent_db_id=parent[
                                        "db_id"
                                    ],  # 使用父任务的数据库ID
                                )

                                # 立即在数据库中创建子任务
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
                            # AI认为是composite但没提供子任务 - 可能是格式问题
                            print(
                                f"[BFS_planner] WARNING: '{parent['name']}' classified as composite but no subtasks provided"
                            )
                            parent["task_type"] = "composite"  # 保持composite状态
                            log_entry["status"] = "composite_no_subtasks"
                            log_entry["action"] = "kept_composite"

                    elif evaluated_type == "atomic":
                        # AI明确判断为atomic
                        print(f"[BFS_planner] Finalizing '{parent['name']}' as atomic")
                        parent["task_type"] = "atomic"
                        log_entry["status"] = "atomic"
                        log_entry["action"] = "marked_atomic"

                    else:
                        # 未知的评估类型
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
                    parent["task_type"] = "atomic"  # 异常时保守处理
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

            # 进入下一层
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

        # 确定停止原因
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
            "plan_id": plan_id,  # 新增：返回计划ID
            "tree": roots,
            "flat_tree": flat_tree,
            "bfs_order": bfs_order,
            "total_tasks": len(flat_tree),
            "max_layer": max((n.get("layer", 0) for n in flat_tree), default=0),
            "layer_distribution": layer_distribution,
            "type_distribution": type_distribution,
            "evaluation_log": evaluation_log,
            "stopped_reason": stopped_reason,
            "task_id_map": task_id_map,  # 新增：返回ID映射关系
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
    - 仅返回纯 JSON（通过 parse_json_obj 解析）
    - 对返回结构做健壮性校验与兜底
    - 异常时写入 basic-fallback 上下文
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
                    # 允许字符串型 section，作为 content
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

        # 软校验：把若干字段规整为基础类型
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
}}
"""
        # 1) LLM 生成并解析
        context_response = client.chat(context_prompt)
        context_result = parse_json_obj(context_response) or {}

        # 2) 取值并做健壮性兜底
        combined = context_result.get("combined")
        if not isinstance(combined, str) or not combined.strip():
            combined = f"Task: {task_data['name']}\n{task_data['prompt']}"

        sections = _coerce_sections(context_result.get("sections"))
        meta = _coerce_meta(context_result.get("meta"))

        # 3) 入库
        repo.upsert_task_context(task_id, combined, sections, meta, label="ai-initial")

    except Exception as e:
        # Fallback：确保任何异常情况下都能落库
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
            # 最后兜底：避免任何异常导致流程崩溃
            pass


def propose_plan_service(
    payload: Dict[str, Any], client: Optional[LLMProvider] = None
) -> Dict[str, Any]:
    """
    基于 BFS_planner 生成完整任务树，并将其持久化。
    现在任务创建是在BFS_planner中完成的，这里只需要调用并返回结果。
    """
    goal = (payload or {}).get("goal") or (payload or {}).get("instruction") or ""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Missing 'goal' in request body")

    client = client or get_default_client()
    repo = default_repo

    # 调用更新后的 BFS_planner，它会直接创建计划和所有任务
    result = BFS_planner(goal, repo=repo, client=client)

    if not result.get("success"):
        return {"title": goal.strip()[:60], "tasks": [], "error": result.get("error")}

    print(
        f"[propose_plan_service] Successfully created plan with {result['total_tasks']} tasks"
    )

    return {
        "success": True,
        "plan_id": result["plan_id"],
        "title": result["title"],
        "goal": goal,
        "total_tasks": result["total_tasks"],
        "max_layer": result.get("max_layer", 0),
        "tree": result["tree"],
        "flat_tree": result["flat_tree"],
        "layer_distribution": result.get("layer_distribution", {}),
        "type_distribution": result.get("type_distribution", {}),
        "stopped_reason": result.get("stopped_reason", "completed"),
    }


def approve_plan_service(
    plan: Dict[str, Any], repo: Optional[TaskRepository] = None
) -> Dict[str, Any]:
    """
    批准计划服务 - 现在主要用于处理已有的计划数据
    由于propose_plan_service已经创建了任务，这个函数现在主要用于兼容性
    """
    if not isinstance(plan, dict):
        raise ValueError("Body must be a JSON object")

    # 如果计划已经有plan_id，说明已经在数据库中了
    if plan.get("plan_id"):
        return {
            "success": True,
            "plan_id": plan["plan_id"],
            "message": "Plan already exists in database",
            "total_tasks": plan.get("total_tasks", 0),
            "max_layer": plan.get("max_layer", 0),
        }

    # 否则按原逻辑处理（用于向后兼容）
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

    # 创建计划
    plan_id = repo.create_plan(title, description=plan.get("goal", ""))

    bfs_order = plan.get("bfs_order") or []
    order_map = {tid: i for i, tid in enumerate(bfs_order)}

    tasks = sorted(
        tasks, key=lambda t: (t.get("layer", 0), order_map.get(t.get("id"), 10**9))
    )

    for idx, task in enumerate(tasks):
        try:
            parent_id = (
                task_id_map.get(task.get("parent_id"))
                if task.get("parent_id")
                else None
            )
            priority = task.get("layer", 0) * 10 + idx

            # 创建任务并处理parent_id
            try:
                task_id = repo.create_task(
                    name=f"{prefix}{task.get('name', f'Task_{idx}')}",
                    status="pending",
                    priority=priority,
                    task_type=task.get("task_type", "composite"),
                    parent_id=parent_id,
                )
            except TypeError:
                # 兼容性处理
                task_id = repo.create_task(
                    name=f"{prefix}{task.get('name', f'Task_{idx}')}",
                    status="pending",
                    priority=priority,
                )

                # 尝试通过其他方式设置 parent_id
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
