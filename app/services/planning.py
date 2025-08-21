from typing import Any, Dict, List, Optional
import os
from enum import Enum

from ..llm import get_default_client
from ..interfaces import LLMProvider, TaskRepository
from ..repository.tasks import default_repo
from ..utils import parse_json_obj, plan_prefix


# parse_json_obj is centralized in app/utils.py

# 递归分解配置
COMPLEXITY_KEYWORDS = {
    "high": ["系统", "架构", "平台", "框架", "完整", "全面", "端到端", "整体", "综合"],
    "medium": ["模块", "组件", "功能", "特性", "集成", "优化", "重构", "扩展"],
    "low": ["修复", "调试", "测试", "文档", "配置", "部署", "更新", "检查"]
}


class TaskType(Enum):
    """任务类型枚举"""
    ROOT = "root"        # 根任务：高层目标，需要分解
    COMPOSITE = "composite"  # 复合任务：中等粒度，可能需要进一步分解
    ATOMIC = "atomic"    # 原子任务：可直接执行


def _debug_on() -> bool:
    """检查是否启用调试模式"""
    v = os.environ.get("DECOMP_DEBUG") or os.environ.get("CONTEXT_DEBUG")
    if not v:
        return False
    v = str(v).strip().lower()
    return v in {"1", "true", "yes", "on"}


def evaluate_task_complexity(task_name: str, task_prompt: str = "") -> str:
    """评估任务复杂度
    
    Args:
        task_name: 任务名称
        task_prompt: 任务描述/提示
        
    Returns:
        "high" | "medium" | "low"
    """
    text = f"{task_name} {task_prompt}".lower()
    
    # 检查高复杂度关键词
    high_score = sum(1 for keyword in COMPLEXITY_KEYWORDS["high"] if keyword in text)
    medium_score = sum(1 for keyword in COMPLEXITY_KEYWORDS["medium"] if keyword in text)
    low_score = sum(1 for keyword in COMPLEXITY_KEYWORDS["low"] if keyword in text)
    
    # 基于关键词密度和任务描述长度判断
    if high_score >= 2 or (high_score >= 1 and len(text) > 100):
        return "high"
    elif low_score >= 2 or (low_score >= 1 and len(text) < 50):
        return "low"
    else:
        return "medium"


def determine_task_type(task: Dict[str, Any], complexity: str = None) -> TaskType:
    """确定任务类型
    
    Args:
        task: 任务信息字典
        complexity: 预计算的复杂度（可选）
        
    Returns:
        TaskType 枚举值
    """
    depth = task.get("depth", 0)
    
    # 如果提供了复杂度参数，基于复杂度和深度判断
    if complexity is not None:
        if depth == 0:
            # 根层级任务
            if complexity == "high":
                return TaskType.ROOT
            elif complexity == "medium":
                return TaskType.COMPOSITE
            else:
                return TaskType.ATOMIC
        elif depth == 1:
            return TaskType.COMPOSITE
        else:
            return TaskType.ATOMIC
    
    # 如果已经有明确的类型标识，优先使用
    existing_type = task.get("task_type", "atomic")
    if existing_type in ["root", "composite", "atomic"]:
        return TaskType(existing_type)
    
    # 基于深度判断（没有复杂度参数时）
    if depth == 0:
        # 根层级任务
        if not complexity:
            task_name = task.get("name", "")
            task_prompt = task.get("prompt", "")
            complexity = evaluate_task_complexity(task_name, task_prompt)
        
        if complexity == "high":
            return TaskType.ROOT
        elif complexity == "medium":
            return TaskType.COMPOSITE
        else:
            return TaskType.ATOMIC
    elif depth == 1:
        # 第一层子任务，通常是复合任务
        return TaskType.COMPOSITE
    else:
        # 深层任务，通常是原子任务
        return TaskType.ATOMIC


def should_decompose_task(task: Dict[str, Any], depth: int = 0) -> bool:
    """判断任务是否需要分解 - 由AI动态决定
    
    Args:
        task: 任务信息
        depth: 当前深度
        
    Returns:
        True 如果需要分解
    """
    task_type = determine_task_type(task)
    
    # 原子任务不需要分解
    if task_type == TaskType.ATOMIC:
        return False
    
    # 根任务和复合任务需要分解，深度由AI在分解过程中决定
    return task_type in [TaskType.ROOT, TaskType.COMPOSITE]


def _build_decomposition_prompt(
    task_name: str,
    task_prompt: str,
    task_type: TaskType,
    max_subtasks: int = None
) -> str:
    """构建任务分解提示 - 由AI动态决定分解粒度
    
    Args:
        task_name: 任务名称
        task_prompt: 任务描述
        task_type: 任务类型
        max_subtasks: 最大子任务数（可选，由AI动态决定）
        
    Returns:
        分解提示字符串
    """
    base_prompt = f"""
请智能分析以下任务的复杂度，并决定是否需要分解以及分解为多少个子任务。

父任务：{task_name}
任务描述：{task_prompt}

要求：
1. 根据任务复杂度智能决定子任务数量（2-8个之间）
2. 每个子任务应该是独立的、可执行的
3. 子任务应该共同完成父任务的目标
4. 避免过于复杂或过于简单的子任务
5. 保持子任务之间的逻辑顺序

返回格式：提供每个子任务的名称和简要描述
"""
    
    if task_type == TaskType.ROOT:
        return base_prompt + "\n\n注意：这是顶层目标，请根据复杂度决定是否需要进一步细分，子任务粒度应该适中。"
    elif task_type == TaskType.COMPOSITE:
        return base_prompt + "\n\n注意：这是复合任务，请决定是否需要分解为可以直接执行的原子任务。"
    else:
        return base_prompt


def recursive_decompose_task(
    task: Dict[str, Any],
    max_subtasks: int = None,
    depth: int = 0,
    repo: Optional[TaskRepository] = None
) -> Dict[str, Any]:
    """递归分解单个任务 - 由AI动态决定分解粒度和深度
    
    Args:
        task: 任务信息
        max_subtasks: 最大子任务数（可选，由AI动态决定）
        depth: 当前深度
        repo: 仓储实例
        
    Returns:
        分解结果
    """
    repo = repo or default_repo
    task_id = task.get("id")
    task_name = task.get("name", "")
    task_prompt = task.get("prompt", "")
    
    try:
        # 检查是否需要分解
        if not should_decompose_task(task, depth):
            return {"success": False, "error": "Task does not need decomposition"}
        
        # 确定任务类型
        task_type = determine_task_type(task)
        
        # 构建分解提示 - 让AI动态决定子任务数量
        decomp_prompt = _build_decomposition_prompt(task_name, task_prompt, task_type)
        
        # 调用规划服务生成子任务 - 不限制数量
        plan_payload = {
            "goal": decomp_prompt,
            "title": f"分解_{task_name}"
        }
        plan_result = propose_plan_service(plan_payload)
        
        # 检查规划服务结果
        if not isinstance(plan_result, dict) or not plan_result.get("tasks"):
            return {"success": False, "error": "Failed to generate subtasks"}
        
        subtasks = plan_result.get("tasks", [])
        if not subtasks:
            return {"success": False, "error": "No subtasks generated"}
        
        # 创建子任务 - 不限制数量，由AI决定
        created_subtasks = []
        for i, subtask in enumerate(subtasks):
            subtask_name = subtask.get("name", f"子任务 {i+1}")
            subtask_priority = subtask.get("priority", 100 + i * 10)
            
            # 让AI动态决定子任务类型
            subtask_complexity = evaluate_task_complexity(subtask_name, subtask.get("prompt", ""))
            if task_type == TaskType.ROOT:
                child_type = TaskType.COMPOSITE.value
            else:
                # 根据子任务复杂度决定类型
                child_type = determine_task_type({
                    "name": subtask_name, 
                    "prompt": subtask.get("prompt", ""),
                    "depth": depth + 1
                }, subtask_complexity).value
            
            # 创建子任务
            subtask_id = repo.create_task(
                name=subtask_name,
                status="pending",
                priority=subtask_priority,
                parent_id=task_id,
                task_type=child_type
            )
            
            # 保存子任务输入
            subtask_prompt = subtask.get("prompt", "")
            if subtask_prompt:
                repo.upsert_task_input(subtask_id, subtask_prompt)
            
            created_subtasks.append({
                "id": subtask_id,
                "name": subtask_name,
                "type": child_type,
                "priority": subtask_priority,
                "depth": depth + 1
            })
        
        return {
            "success": True,
            "task_id": task_id,
            "subtasks": created_subtasks,
            "decomposition_depth": depth + 1
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def bfs_decompose_task(
    root_task: Dict[str, Any],
    repo: Optional[TaskRepository] = None
) -> Dict[str, Any]:
    """BFS广度优先分解任务 - 逐层判断是否需要分解
    
    Args:
        root_task: 根任务信息
        repo: 仓储实例
        
    Returns:
        BFS分解结果
    """
    repo = repo or default_repo
    root_id = root_task.get("id")
    
    try:
        from collections import deque
        
        queue = deque([(root_task, 0)])  # (task, current_depth)
        total_created = 0
        level_stats = {}
        max_depth = 0
        
        while queue:
            current_task, current_depth = queue.popleft()
            max_depth = max(max_depth, current_depth)
            
            # 检查是否需要分解 - 逐层判断
            if not should_decompose_task(current_task, current_depth):
                continue
            
            # 使用递归分解逻辑，但按BFS方式执行
            decomp_result = recursive_decompose_task(
                current_task,
                depth=current_depth,
                repo=repo
            )
            
            if decomp_result.get("success"):
                subtasks = decomp_result.get("subtasks", [])
                level_stats[current_depth + 1] = len(subtasks)
                total_created += len(subtasks)
                
                # 将新创建的子任务加入队列，继续BFS
                for subtask in subtasks:
                    subtask_full = {
                        "id": subtask["id"],
                        "name": subtask["name"],
                        "prompt": repo.get_task_input_prompt(subtask["id"]) or "",
                        "depth": subtask.get("depth", current_depth + 1)
                    }
                    queue.append((subtask_full, current_depth + 1))
        
        return {
            "success": True,
            "root_task_id": root_id,
            "total_subtasks_created": total_created,
            "level_distribution": level_stats,
            "strategy": "bfs",
            "actual_depth": max_depth,
            "decomposition_stopped_reason": "no_more_decomposable_tasks"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def bfs_decompose_plan(
    plan: Dict[str, Any],
    repo: Optional[TaskRepository] = None
) -> Dict[str, Any]:
    """BFS广度优先分解整个plan - 由AI动态决定分解深度
    
    Args:
        plan: plan信息
        repo: 仓储实例
        
    Returns:
        BFS分解统计信息
    """
    repo = repo or default_repo
    
    try:
        title = plan.get("title", "")
        prefix = plan_prefix(title)
        root_tasks = _get_tasks_by_prefix(prefix, repo)
        
        # 过滤出根任务（没有父任务的任务）
        root_tasks = [t for t in root_tasks if not t.get("parent_id")]
        
        if not root_tasks:
            return {"success": False, "error": "No root tasks found for plan"}
        
        total_created = 0
        task_results = []
        
        # 对每个根任务进行BFS分解 - 由AI动态决定深度
        for root_task in root_tasks:
            result = bfs_decompose_task(root_task, repo)
            task_results.append(result)
            if result.get("success"):
                total_created += result.get("total_subtasks_created", 0)
        
        return {
            "success": True,
            "plan_title": title,
            "total_root_tasks": len(root_tasks),
            "total_subtasks_created": total_created,
            "per_task_results": task_results,
            "strategy": "bfs"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def recursive_decompose_plan(
    plan: Dict[str, Any],
    max_subtasks: int = None,
    repo: Optional[TaskRepository] = None
) -> Dict[str, Any]:
    """递归分解整个plan - 由AI动态决定分解粒度和深度
    
    Args:
        plan: plan信息
        max_subtasks: 每个任务的最大子任务数（可选，由AI动态决定）
        repo: 仓储实例
        
    Returns:
        分解统计信息
    """
    repo = repo or default_repo
    
    try:
        title = plan.get("title", "")
        prefix = plan_prefix(title)
        tasks = _get_tasks_by_prefix(prefix, repo)
        
        if not tasks:
            return {"success": False, "error": "No tasks found for plan"}
        
        total_decomposed = 0
        total_subtasks = 0
        
        # 对每个任务进行递归分解 - 由AI动态决定分解深度
        for task in tasks:
            # 跳过已经有子任务的任务
            if task.get("parent_id"):
                continue
                
            # 递归分解 - 不限制深度，由AI决定
            result = recursive_decompose_task(
                task,
                max_subtasks=max_subtasks,
                depth=0,
                repo=repo
            )
            
            if result.get("success"):
                total_decomposed += 1
                total_subtasks += len(result.get("subtasks", []))
        
        return {
            "success": True,
            "plan_title": title,
            "tasks_decomposed": total_decomposed,
            "total_subtasks_created": total_subtasks,
            "strategy": "recursive"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_tasks_by_prefix(prefix: str, repo: Optional[TaskRepository] = None) -> List[Dict[str, Any]]:
    """通过前缀获取任务列表的辅助函数"""
    repo = repo or default_repo
    return repo.list_tasks_by_prefix(prefix)


def propose_plan_service(payload: Dict[str, Any], client: Optional[LLMProvider] = None) -> Dict[str, Any]:
    """
    Build a plan via LLM with normalization. Returns { title, tasks }.
    Does not persist anything.
    """
    goal = (payload or {}).get("goal") or (payload or {}).get("instruction") or ""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("Missing 'goal' in request body")
    title = (payload or {}).get("title") or goal.strip()[:60]
    sections = (payload or {}).get("sections")
    style = (payload or {}).get("style") or ""
    notes = (payload or {}).get("notes") or ""
    
    # 分解策略选项 - 默认启用递归分解
    enable_decomposition = bool((payload or {}).get("enable_decomposition", True))
    enable_bfs_decomposition = bool((payload or {}).get("enable_bfs_decomposition", False))

    # AI自动决定sections数量
    if sections is None:
        sections_instruction = "Determine the optimal number of tasks (typically 3-8) based on the complexity and scope of the goal."
    else:
        sections_instruction = f"Preferred number of tasks: {sections} (4-8 typical)."

    prompt = (
        "You are an expert project planner. Break down the user's goal into a small set of actionable tasks.\n"
        "Return ONLY a JSON object with this schema: {\n"
        "  \"title\": string,\n"
        "  \"tasks\": [ { \"name\": string, \"prompt\": string } ]\n"
        "}\n"
        f"Goal: {goal}\n"
        f"{sections_instruction}\n"
        f"Style (optional): {style}\n"
        f"Notes (optional): {notes}\n"
        "Rules: Do not include markdown code fences. Keep concise prompts for each task."
    )

    plan: Dict[str, Any]
    client = client or get_default_client()
    try:
        content = client.chat(prompt)
        obj = parse_json_obj(content) or {}
        if isinstance(obj, list):
            plan = {"title": title, "tasks": obj}
        elif isinstance(obj, dict):
            plan = {"title": obj.get("title") or title, "tasks": obj.get("tasks") or []}
        else:
            plan = {"title": title, "tasks": []}
    except Exception:
        plan = {"title": title, "tasks": []}

    # Normalize tasks and compute priorities
    raw_tasks = plan.get("tasks") or []
    norm_tasks: List[Dict[str, Any]] = []
    for idx, t in enumerate(raw_tasks):
        try:
            name = str(t.get("name") if isinstance(t, dict) else t).strip()
        except Exception:
            name = f"Task {idx+1}"
        if not name:
            name = f"Task {idx+1}"
        default_prompt = (
            f"Fulfill this part of the overall goal.\n"
            f"Overall goal: {goal}\n"
            f"Task: {name}.\n"
            f"Write ~200 words with clear, actionable content."
        )
        prompt_t = t.get("prompt") if isinstance(t, dict) else None
        if not isinstance(prompt_t, str) or not prompt_t.strip():
            prompt_t = default_prompt
        norm_tasks.append({
            "name": name,
            "prompt": prompt_t,
            "priority": (idx + 1) * 10,
            "task_type": determine_task_type({"name": name, "prompt": prompt_t, "depth": 0}).value,
            "enable_decomposition": enable_decomposition,
            "enable_bfs_decomposition": enable_bfs_decomposition
        })

    # 使用AI驱动的任务分解 - 让LLM智能判断是否分解
    decomposed_tasks = []
    
    for idx, task in enumerate(norm_tasks):
        task_name = task.get("name", "")
        task_prompt = task.get("prompt", "")
        
        # 评估复杂度
        complexity = evaluate_task_complexity(task_name, task_prompt)
        task_type = determine_task_type({"name": task_name, "prompt": task_prompt}, complexity)
        
        # 让AI决定是否分解以及分解方式
        should_decompose = task_type in [TaskType.ROOT, TaskType.COMPOSITE]
        
        if should_decompose:
            # 使用LLM智能分解任务
            decomp_prompt = f"""
你是一个专业的项目规划师。请分析以下任务，并智能决定是否需要进行分解。

父任务：{task_name}
任务描述：{task_prompt}
任务复杂度：{complexity}
任务类型：{task_type.value}

分析要点：
- 这是一个{complexity}复杂度的{task_type.value}类型任务
- 如果任务过于复杂或宽泛，应该分解为更小、更具体的子任务
- 如果任务已经很具体和可执行，可以保持原样

请返回一个JSON对象，格式如下：
如果决定分解：
{{
  "tasks": [
    {{"name": "子任务1名称", "prompt": "子任务1的具体描述"}},
    {{"name": "子任务2名称", "prompt": "子任务2的具体描述"}}
  ]
}}

如果决定不分解：
{{
  "tasks": [{{"name": "{task_name}", "prompt": "{task_prompt}"}}]
}}

记住：只有真正需要分解的复杂任务才进行分解，简单任务保持原样。
"""
            
            try:
                # 调用LLM进行智能分解
                decomp_result = client.chat(decomp_prompt)
                decomp_obj = parse_json_obj(decomp_result) or {}
                
                # 更灵活的响应格式处理
                subtasks = []
                if isinstance(decomp_obj, dict):
                    # 支持多种响应格式
                    tasks_data = decomp_obj.get("tasks") or decomp_obj.get("subtasks") or decomp_obj.get("分解结果")
                    if isinstance(tasks_data, list):
                        subtasks = tasks_data
                    elif tasks_data:
                        subtasks = [tasks_data]
                elif isinstance(decomp_obj, list):
                    # LLM直接返回任务列表
                    subtasks = decomp_obj
                
                # 确保子任务格式正确
                formatted_subtasks = []
                for st in subtasks:
                    if isinstance(st, dict):
                        formatted_subtasks.append({
                            "name": st.get("name", "未命名子任务"),
                            "prompt": st.get("prompt", st.get("description", "完成子任务"))
                        })
                    elif isinstance(st, str):
                        formatted_subtasks.append({
                            "name": str(st),
                            "prompt": f"完成{str(st)}"
                        })
                
                # 如果没有有效的子任务，使用原始任务
                if not formatted_subtasks:
                    formatted_subtasks = [{"name": task_name, "prompt": task_prompt}]
                    
                subtasks = formatted_subtasks
                    
            except Exception as e:
                # 出错时使用原始任务
                print(f"LLM分解失败: {e}")
                subtasks = [{"name": task_name, "prompt": task_prompt}]
        else:
            # 不需要分解的任务
            subtasks = [task]
        
        # 添加子任务，使用LLM生成的结果
        for i, subtask in enumerate(subtasks):
            if isinstance(subtask, dict):
                subtask_name = subtask.get("name", f"子任务 {i+1}")
                subtask_prompt = subtask.get("prompt", f"完成{task_name}的子任务")
            else:
                subtask_name = str(subtask)
                subtask_prompt = f"完成{subtask_name}"
            
            decomposed_tasks.append({
                "name": subtask_name,
                "prompt": subtask_prompt,
                "priority": task.get("priority", 0) + i * 10,
                "task_type": "atomic",
                "parent_task": task_name if len(subtasks) > 1 else None,
                "original_task": task_name
            })
    
    result = {
        "title": plan.get("title") or title,
        "tasks": decomposed_tasks,
        "total_original_tasks": len(norm_tasks),
        "total_decomposed_tasks": len(decomposed_tasks),
        "decomposition_applied": True
    }
    
    return result
    


def approve_plan_service(plan: Dict[str, Any], repo: Optional[TaskRepository] = None) -> Dict[str, Any]:
    """
    Persist tasks from plan into DB with name prefixing by [title].
    Optional hierarchical mode: if plan contains {"hierarchical": true}, create a root task
    and attach all tasks as children (parent_id=root_id).
    Optional recursive decomposition: if plan contains {"enable_decomposition": true},
    automatically decompose complex tasks.
    Returns { plan: { title }, created: [ {id, name, priority} ], (root_id?), (decomposition?) }.
    """
    if not isinstance(plan, dict):
        raise ValueError("Body must be a JSON object")
    title = (plan.get("title") or "Untitled").strip()
    tasks = plan.get("tasks") or []
    if not tasks:
        raise ValueError("Plan has no tasks to approve")

    prefix = plan_prefix(title)
    created: List[Dict[str, Any]] = []
    repo = repo or default_repo

    # Optional hierarchical mode (default False for backward compatibility)
    hierarchical = bool(plan.get("hierarchical"))
    root_id: Optional[int] = None
    if hierarchical:
        root_label = str(plan.get("root_label") or "Plan Root").strip()
        try:
            root_priority = int(plan.get("root_priority")) if plan.get("root_priority") is not None else None
        except Exception:
            root_priority = None
        root_name = f"{prefix}{root_label}"  # e.g., "[Title] Plan Root"
        # Do not pass parent_id for root creation to keep compatibility with repos without parent_id arg
        root_id = repo.create_task(root_name, status="pending", priority=root_priority, task_type=TaskType.ROOT.value)
        repo.upsert_task_input(root_id, f"Root task node for plan '{title}'.")

    # 允许在批准阶段也进行分解
    enable_decomposition = bool(plan.get("enable_decomposition", True))
    enable_bfs_decomposition = bool(plan.get("enable_bfs_decomposition", False))

    for idx, t in enumerate(tasks):
        name = (t.get("name") or "").strip() if isinstance(t, dict) else str(t)
        if not name:
            continue
        prompt_t = t.get("prompt") if isinstance(t, dict) else None
        if not isinstance(prompt_t, str) or not prompt_t.strip():
            prompt_t = f"Write a focused section for: {name}"
        try:
            priority = int(t.get("priority")) if isinstance(t, dict) and t.get("priority") is not None else None
        except Exception:
            priority = None
        if priority is None:
            priority = (idx + 1) * 10

        # 任务类型已在生成阶段确定
        task_type = t.get("task_type", TaskType.ATOMIC.value)

        # Only pass parent_id when hierarchical mode is enabled to preserve backward compatibility
        if hierarchical and root_id is not None:
            task_id = repo.create_task(prefix + name, status="pending", priority=priority, parent_id=root_id, task_type=task_type)
        else:
            task_id = repo.create_task(prefix + name, status="pending", priority=priority, task_type=task_type)
        repo.upsert_task_input(task_id, prompt_t)
        created.append({"id": task_id, "name": name, "priority": priority, "task_type": task_type})

    out = {"plan": {"title": title}, "created": created}
    if hierarchical and root_id is not None:
        out["root_id"] = root_id

    # Perform decomposition based on strategy
    if enable_bfs_decomposition:
        bfs_result = bfs_decompose_plan(
            plan,
            repo=repo
        )
        out["bfs_decomposition"] = bfs_result
    elif enable_decomposition:
        decomposition_result = recursive_decompose_plan(
            plan,
            repo=repo
        )
        out["recursive_decomposition"] = decomposition_result

    return out
