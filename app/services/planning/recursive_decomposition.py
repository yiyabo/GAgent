"""
递归任务分解模块

基于任务复杂度智能分解任务为子任务的核心逻辑。
支持 ROOT → COMPOSITE → ATOMIC 三级分解架构。
"""

import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from ..planning import propose_plan_service
from ...utils.task_path_generator import get_task_file_path, ensure_task_directory
from ...utils import plan_prefix
from app.services.foundation.settings import get_settings

# Task complexity evaluation thresholds
COMPLEXITY_KEYWORDS = {
    "high": ["系统", "架构", "平台", "框架", "完整", "全面", "端到端", "整体", "综合"],
    "medium": ["模块", "组件", "功能", "特性", "集成", "优化", "重构", "扩展"],
    "low": ["修复", "调试", "测试", "文档", "配置", "部署", "更新", "检查"],
}

MAX_DECOMPOSITION_DEPTH = 3  # 最大分解深度
MIN_ATOMIC_TASKS = 2  # 最小原子任务数
MAX_ATOMIC_TASKS = 8  # 最大原子任务数

_DECOMP_LOGGER = logging.getLogger("app.decomposition")


class TaskType(Enum):
    """任务类型枚举"""

    ROOT = "root"  # 根任务：高层目标，需要分解
    COMPOSITE = "composite"  # 复合任务：中等粒度，可能需要进一步分解
    ATOMIC = "atomic"  # 原子任务：可直接执行


def _debug_on() -> bool:
    """检查是否启用调试模式（集中配置）"""
    try:
        s = get_settings()
        return bool(getattr(s, "decomp_debug", False) or getattr(s, "ctx_debug", False))
    except Exception:
        v = os.environ.get("DECOMP_DEBUG") or os.environ.get("CONTEXT_DEBUG")
        return str(v).strip().lower() in {"1", "true", "yes", "on"} if v else False


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
            task_prompt = ""  # 可以从 repo 获取，暂时简化
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


def should_decompose_task(task: Dict[str, Any], repo: TaskRepository = None) -> bool:
    """判断任务是否需要分解

    Args:
        task: 任务信息
        repo: 仓储实例

    Returns:
        True 如果需要分解
    """
    if repo is None:
        repo = default_repo

    task_id = task.get("id")
    depth = task.get("depth", 0)
    task_type = determine_task_type(task)

    # 检查分解深度限制（深度从0开始，所以depth=2已经是第3层）
    if depth >= MAX_DECOMPOSITION_DEPTH - 1:
        return False

    # 原子任务不需要分解
    if task_type == TaskType.ATOMIC:
        return False

    # 检查是否已经有子任务
    try:
        children = repo.get_children(task_id)
        if children:
            # 已有子任务，检查是否需要进一步分解
            pending_children = [c for c in children if c.get("status") == "pending"]
            if len(pending_children) >= MIN_ATOMIC_TASKS:
                return False  # 已有足够的子任务
    except Exception as e:
        if _debug_on():
            _DECOMP_LOGGER.debug({"event": "should_decompose_task.get_children_error", "error": str(e)})
        # 如果获取子任务失败，假设没有子任务，继续分解判断
        pass

    # 根任务和复合任务需要分解
    return task_type in [TaskType.ROOT, TaskType.COMPOSITE]


def decompose_task(
    task_id: int, repo: TaskRepository = None, max_subtasks: int = MAX_ATOMIC_TASKS, force: bool = False
) -> Dict[str, Any]:
    """递归分解任务

    Args:
        task_id: 要分解的任务ID
        repo: 仓储实例
        max_subtasks: 最大子任务数
        force: 强制分解（忽略现有子任务）

    Returns:
        分解结果字典
    """
    if repo is None:
        repo = default_repo

    task = repo.get_task_info(task_id)
    if not task:
        return {"success": False, "error": "Task not found"}

    if not force and not should_decompose_task(task, repo):
        return {"success": False, "error": "Task does not need decomposition"}

    task_name = task.get("name", "")
    task_type = determine_task_type(task)

    if _debug_on():
        _DECOMP_LOGGER.debug(
            {
                "event": "decompose_task.start",
                "task_id": task_id,
                "task_name": task_name,
                "task_type": task_type.value,
                "depth": task.get("depth", 0),
            }
        )

    try:
        # 获取任务输入提示作为分解上下文
        task_prompt = repo.get_task_input_prompt(task_id) or ""

        # 构建分解提示
        decomp_prompt = _build_decomposition_prompt(task_name, task_prompt, task_type, max_subtasks)

        # 调用规划服务生成子任务
        plan_payload = {"goal": decomp_prompt, "title": f"分解_{task_name}", "sections": max_subtasks}
        plan_result = propose_plan_service(plan_payload)

        # 检查规划服务结果
        if not isinstance(plan_result, dict) or not plan_result.get("tasks"):
            return {"success": False, "error": "Failed to generate subtasks"}

        subtasks = plan_result.get("tasks", [])
        if not subtasks:
            return {"success": False, "error": "No subtasks generated"}

        # 创建子任务
        created_subtasks = []
        for i, subtask in enumerate(subtasks[:max_subtasks]):
            subtask_name = subtask.get("name", f"子任务 {i+1}")
            subtask_priority = subtask.get("priority", 100 + i * 10)

            # 确定子任务类型
            parent_depth = task.get("depth", 0)
            if task_type == TaskType.ROOT:
                child_type = TaskType.COMPOSITE.value
            elif parent_depth >= MAX_DECOMPOSITION_DEPTH - 2:
                child_type = TaskType.ATOMIC.value
            else:
                child_type = TaskType.COMPOSITE.value

            # 创建子任务
            # CRITICAL FIX: Add plan prefix to subtask names to associate them with the plan.
            from ...utils import split_prefix
            plan_title, _ = split_prefix(task_name)
            if not plan_title and task.get("depth", 0) == 0:
                plan_title = task_name # The root task's name is the plan title
            
            prefix = plan_prefix(plan_title) if plan_title else ""
            
            subtask_id = repo.create_task(
                name=prefix + subtask_name, status="pending", priority=subtask_priority, parent_id=task_id, task_type=child_type
            )

            # 保存子任务输入
            subtask_prompt = subtask.get("prompt", "")
            if subtask_prompt:
                repo.upsert_task_input(subtask_id, subtask_prompt)

            # 新增：为COMPOSITE/ATOMIC自动创建结果目录或占位md文件
            try:
                child_info = repo.get_task_info(subtask_id)
                child_path = get_task_file_path(child_info, repo)
                # COMPOSITE → 目录 + summary.md
                if child_info.get("task_type") == "composite":
                    if ensure_task_directory(child_path):
                        summary_md = os.path.join(child_path, "summary.md")
                        if not os.path.exists(summary_md):
                            with open(summary_md, "w", encoding="utf-8") as f:
                                f.write(f"# {subtask_name} — 阶段总结\n\n此文档将聚合该 COMPOSITE 下所有 ATOMIC 的输出，以形成阶段总结。\n")
                # ATOMIC → 文件占位
                elif child_info.get("task_type") == "atomic":
                    ensure_task_directory(child_path)
                    if not os.path.exists(child_path):
                        with open(child_path, "w", encoding="utf-8") as f:
                            f.write(f"# {subtask_name}\n\n(自动生成的任务文档，执行完成后将写入内容)\n")
            except Exception as e:
                _DECOMP_LOGGER.warning({
                    "event": "decompose_task.files_init_failed",
                    "task_id": task_id,
                    "child_id": subtask_id,
                    "error": str(e)
                })

            created_subtasks.append(
                {"id": subtask_id, "name": subtask_name, "type": child_type, "task_type": child_type, "priority": subtask_priority}  # ⭐ 前端需要task_type字段
            )

        # 更新父任务类型（如果需要）
        if task.get("task_type") == "atomic":
            repo.update_task_type(task_id, task_type.value)

        if _debug_on():
            _DECOMP_LOGGER.debug(
                {"event": "decompose_task.success", "task_id": task_id, "subtasks_created": len(created_subtasks)}
            )

        return {
            "success": True,
            "task_id": task_id,
            "subtasks": created_subtasks,
            "decomposition_depth": task.get("depth", 0) + 1,
        }

    except Exception as e:
        if _debug_on():
            _DECOMP_LOGGER.error({"event": "decompose_task.error", "task_id": task_id, "error": str(e)})
        return {"success": False, "error": str(e)}


def _build_decomposition_prompt(task_name: str, task_prompt: str, task_type: TaskType, max_subtasks: int) -> str:
    """构建任务分解提示

    Args:
        task_name: 任务名称
        task_prompt: 任务描述
        task_type: 任务类型
        max_subtasks: 最大子任务数

    Returns:
        分解提示字符串
    """
    if task_type == TaskType.ROOT:
        decomp_instruction = f"""
请将以下根任务分解为 {MIN_ATOMIC_TASKS}-{max_subtasks} 个主要的功能模块或阶段：

任务名称：{task_name}
任务描述：{task_prompt}

分解原则：
1. 每个子任务应该是一个相对独立的功能模块或实现阶段
2. 子任务之间应该有清晰的边界和职责划分
3. 优先级应该反映实现的先后顺序和重要性
4. 每个子任务的名称应该简洁明确，描述应该详细具体

请按照以下格式返回分解结果：
"""
    elif task_type == TaskType.COMPOSITE:
        decomp_instruction = f"""
请将以下复合任务进一步分解为 {MIN_ATOMIC_TASKS}-{max_subtasks} 个具体的实现步骤：

任务名称：{task_name}
任务描述：{task_prompt}

分解原则：
1. 每个子任务应该是一个具体的实现步骤或技术任务
2. 子任务应该是可以直接执行的原子操作
3. 优先级应该反映执行的依赖关系和重要性
4. 每个子任务应该有明确的输入、输出和验收标准

请按照以下格式返回分解结果：
"""
    else:
        # 原子任务不应该被分解，返回空提示
        return ""

    return decomp_instruction


def recursive_decompose_plan(
    plan_title: str, repo: TaskRepository = None, max_depth: int = MAX_DECOMPOSITION_DEPTH
) -> Dict[str, Any]:
    """递归分解整个计划

    Args:
        plan_title: 计划标题
        repo: 仓储实例
        max_depth: 最大分解深度

    Returns:
        分解结果字典
    """
    if repo is None:
        repo = default_repo

    try:
        # 获取计划中的所有任务
        plan_tasks = repo.list_plan_tasks(plan_title)

        decomposition_results = []
        processed_tasks = set()  # 避免重复处理

        # 多轮分解，直到没有新的任务需要分解
        round_count = 0
        while round_count < max_depth:
            round_count += 1
            current_round_decomposed = False

            # 重新获取计划任务（包括新创建的子任务）
            plan_tasks = repo.list_plan_tasks(plan_title)

            for task in plan_tasks:
                task_id = task.get("id")
                depth = task.get("depth", 0)

                # 跳过已处理的任务
                if task_id in processed_tasks:
                    continue

                # 检查深度限制
                if depth >= max_depth - 1:
                    continue

                # 尝试分解任务
                if should_decompose_task(task, repo):
                    result = decompose_task(task_id, repo)
                    if result.get("success"):
                        decomposition_results.append(result)
                        processed_tasks.add(task_id)
                        current_round_decomposed = True

            # 如果这一轮没有分解任何任务，停止递归
            if not current_round_decomposed:
                break

        return {
            "success": True,
            "plan_title": plan_title,
            "decompositions": decomposition_results,
            "total_tasks_decomposed": len(decomposition_results),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
