"""
Tool-Aware Task Decomposition

This module provides tool-aware task decomposition capabilities that consider
available tool capabilities when breaking down complex tasks.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional
import os

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from ...utils import plan_prefix, split_prefix
from ...utils.task_path_generator import get_task_file_path, ensure_task_directory
from .recursive_decomposition import (
    MAX_DECOMPOSITION_DEPTH,
    TaskType,
    _build_decomposition_prompt,
    determine_task_type,
    evaluate_task_complexity,
)
from .planning import propose_plan_service

logger = logging.getLogger(__name__)


class ToolRequirement(Enum):
    """Tool requirement types for task decomposition"""

    INFORMATION_RETRIEVAL = "information_retrieval"  # Needs web search
    DATA_PROCESSING = "data_processing"  # Needs database operations
    FILE_MANAGEMENT = "file_management"  # Needs file operations
    NONE = "none"  # No external tools needed


class ToolAwareTaskDecomposer:
    """Tool-aware task decomposition with intelligent capability analysis"""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.repo = repo or default_repo
        self.tool_router = None

    async def initialize(self):
        """Initialize the decomposer with tool capabilities"""
        try:
            from tool_box import get_smart_router
            
            # 只有在未初始化时才获取router
            if self.tool_router is None:
                self.tool_router = await get_smart_router()
                logger.info("Tool-aware decomposer initialized")
        except Exception as e:
            logger.warning(f"Tool router initialization failed: {e}")

    async def decompose_with_tool_awareness(
        self, task_id: int, max_subtasks: int = 8, force: bool = False
    ) -> Dict[str, Any]:
        """
        Decompose task with consideration of tool capabilities

        Args:
            task_id: Task to decompose
            max_subtasks: Maximum number of subtasks
            force: Force decomposition even if task has subtasks

        Returns:
            Decomposition result with tool-aware planning
        """
        if not self.tool_router:
            await self.initialize()

        try:
            # Get task information
            task = self.repo.get_task_info(task_id)
            if not task:
                return {"success": False, "error": "Task not found"}

            task_name = task.get("name", "")
            task_prompt = self.repo.get_task_input_prompt(task_id) or ""

            # Analyze tool requirements for the task
            tool_requirements = await self._analyze_task_tool_needs(task_name, task_prompt)

            # Determine decomposition strategy based on tool capabilities
            decomp_strategy = self._determine_tool_aware_strategy(task, tool_requirements, max_subtasks)

            logger.info(f"Task {task_id} tool requirements: {tool_requirements}")
            logger.info(f"Decomposition strategy: {decomp_strategy}")

            # Execute decomposition with tool awareness
            result = await self._execute_tool_aware_decomposition(task_id, task, decomp_strategy, max_subtasks)

            # Enhance result with tool-aware metadata
            if result.get("success"):
                result["tool_awareness"] = {
                    "tool_requirements": tool_requirements,
                    "strategy": decomp_strategy,
                    "tool_enhanced": True,
                }

            return result

        except Exception as e:
            logger.error(f"Tool-aware decomposition failed for task {task_id}: {e}")
            return {"success": False, "error": str(e)}

    async def _analyze_task_tool_needs(self, task_name: str, task_prompt: str) -> Dict[str, Any]:
        """Analyze what tools the task might need"""
        try:
            if not self.tool_router:
                return {"requirements": [], "confidence": 0.0}

            # Use tool router to analyze requirements
            analysis_prompt = f"""
任务名称: {task_name}
任务描述: {task_prompt}

分析这个任务是否需要外部工具支持，以及需要什么类型的工具。
"""

            routing_result = await self.tool_router.route_request(analysis_prompt)

            # Extract tool requirements
            tool_calls = routing_result.get("tool_calls", [])
            requirements = []

            for call in tool_calls:
                tool_name = call.get("tool_name")
                if tool_name == "web_search":
                    requirements.append(ToolRequirement.INFORMATION_RETRIEVAL)
                elif tool_name == "database_query":
                    requirements.append(ToolRequirement.DATA_PROCESSING)
                elif tool_name == "file_operations":
                    requirements.append(ToolRequirement.FILE_MANAGEMENT)

            if not requirements:
                requirements.append(ToolRequirement.NONE)

            return {
                "requirements": [req.value for req in set(requirements)],
                "confidence": routing_result.get("confidence", 0.0),
                "analysis": routing_result.get("analysis", {}),
                "suggested_tools": [call.get("tool_name") for call in tool_calls],
            }

        except Exception as e:
            logger.warning(f"Tool requirement analysis failed: {e}")
            return {"requirements": [], "confidence": 0.0}

    def _determine_tool_aware_strategy(
        self, task: Dict[str, Any], tool_requirements: Dict[str, Any], max_subtasks: int
    ) -> Dict[str, Any]:
        """Determine decomposition strategy based on tool requirements"""

        task_type = determine_task_type(task)
        requirements = tool_requirements.get("requirements", [])
        confidence = tool_requirements.get("confidence", 0.0)

        # Base strategy on task type and tool needs
        if task_type == TaskType.ROOT:
            if ToolRequirement.INFORMATION_RETRIEVAL.value in requirements:
                # Tasks needing external info should have info-gathering subtasks
                strategy_type = "information_focused"
                suggested_subtasks = min(max_subtasks, 6)  # More subtasks for info gathering
            elif ToolRequirement.DATA_PROCESSING.value in requirements:
                # Tasks needing data processing should have analysis-focused subtasks
                strategy_type = "data_focused"
                suggested_subtasks = min(max_subtasks, 5)
            else:
                strategy_type = "standard"
                suggested_subtasks = min(max_subtasks, 4)
        else:
            # Composite and atomic tasks
            strategy_type = "simple"
            suggested_subtasks = min(max_subtasks, 3)

        return {
            "type": strategy_type,
            "suggested_subtasks": suggested_subtasks,
            "tool_requirements": requirements,
            "confidence": confidence,
            "include_tool_setup": len(requirements) > 1,  # Multi-tool tasks need setup
            "include_result_integration": ToolRequirement.INFORMATION_RETRIEVAL.value in requirements,
        }

    async def _execute_tool_aware_decomposition(
        self, task_id: int, task: Dict[str, Any], strategy: Dict[str, Any], max_subtasks: int
    ) -> Dict[str, Any]:
        """Execute decomposition with tool-aware enhancements"""

        task_name = task.get("name", "")
        task_prompt = self.repo.get_task_input_prompt(task_id) or ""
        task_type = determine_task_type(task)

        # Build enhanced decomposition prompt
        enhanced_prompt = self._build_tool_aware_prompt(task_name, task_prompt, task_type, strategy, max_subtasks)

        # Fallback: if prompt is empty (e.g., atomic task with no tool guidance),
        # force a composite-style prompt to allow a simple breakdown instead of failing.
        if not isinstance(enhanced_prompt, str) or not enhanced_prompt.strip():
            enhanced_prompt = _build_decomposition_prompt(
                task_name, task_prompt, TaskType.COMPOSITE, max_subtasks
            ) or f"请将以下任务分解为 2-{max_subtasks} 个具体步骤：\n任务名称：{task_name}\n任务描述：{task_prompt}\n"

        # Use planning service for decomposition

        plan_payload = {
            "goal": enhanced_prompt,
            "title": f"工具增强_分解_{task_name}",
            "sections": strategy["suggested_subtasks"],
        }

        try:
            plan_result = propose_plan_service(plan_payload)
        except Exception as e:
            logger.warning(f"LLM plan generation failed, using simple fallback: {e}")
            # Simple mechanical fallback to avoid hard failure under rate limiting
            simple_count = max(2, min(strategy.get("suggested_subtasks", 3), max_subtasks))
            fallback_tasks = []
            for i in range(simple_count):
                nm = f"{task_name}-子任务{i+1}"
                pr = 100 + i * 10
                pp = (
                    f"围绕父任务‘{task_name}’完成第{i+1}步具体工作。\n"
                    f"父任务描述：{(task_prompt or '').strip()}\n"
                    "请给出清晰的子步骤、输入与输出要点（150-300字）。"
                )
                fallback_tasks.append({"name": nm, "prompt": pp, "priority": pr})
            plan_result = {"title": f"工具增强_分解_{task_name}", "tasks": fallback_tasks}

        if not isinstance(plan_result, dict) or not plan_result.get("tasks"):
            return {"success": False, "error": "Failed to generate tool-aware subtasks"}

        # Create subtasks with tool awareness
        subtasks = plan_result.get("tasks", [])
        created_subtasks = []

        for i, subtask in enumerate(subtasks[:max_subtasks]):
            subtask_name = subtask.get("name", f"工具增强子任务 {i+1}")
            subtask_priority = subtask.get("priority", 100 + i * 10)

            # Determine child task type
            parent_depth = task.get("depth", 0)
            if task_type == TaskType.ROOT:
                child_type = TaskType.COMPOSITE.value
            else:
                child_type = TaskType.ATOMIC.value

            # Create subtask (ensure plan association via prefix)
            try:
                plan_title, short = split_prefix(task_name)
            except Exception:
                plan_title, short = "", task_name
            prefix = plan_prefix(plan_title) if plan_title else ""
            subtask_id = self.repo.create_task(
                name=prefix + subtask_name,
                status="pending",
                priority=subtask_priority,
                parent_id=task_id,
                task_type=child_type,
            )

            # Enhance subtask prompt with Root Brief, parent chain, and tool context
            original_prompt = subtask.get("prompt", "")
            enhanced_subtask_prompt = self._enhance_subtask_prompt(original_prompt, strategy, i, task_id)

            if enhanced_subtask_prompt:
                self.repo.upsert_task_input(subtask_id, enhanced_subtask_prompt)

            # 新增：为COMPOSITE/ATOMIC自动创建结果目录或占位md文件（与标准分解一致）
            try:
                child_info = self.repo.get_task_info(subtask_id)
                child_path = get_task_file_path(child_info, self.repo)
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
                logger.warning(
                    {
                        "event": "tool_aware_decompose.files_init_failed",
                        "task_id": task_id,
                        "child_id": subtask_id,
                        "error": str(e),
                    }
                )

            created_subtasks.append(
                {
                    "id": subtask_id,
                    "name": subtask_name,
                    "type": child_type,
                    "task_type": child_type,  # ⭐ 前端需要task_type字段
                    "priority": subtask_priority,
                    "tool_enhanced": True,
                }
            )

        # Update parent task type
        if task.get("task_type") == "atomic":
            self.repo.update_task_type(task_id, task_type.value)

        return {
            "success": True,
            "task_id": task_id,
            "subtasks": created_subtasks,
            "decomposition_depth": task.get("depth", 0) + 1,
            "tool_strategy": strategy,
            "enhancement_type": "tool_aware",
        }

    def _build_tool_aware_prompt(
        self, task_name: str, task_prompt: str, task_type: TaskType, strategy: Dict[str, Any], max_subtasks: int
    ) -> str:
        """Build tool-aware decomposition prompt"""

        base_prompt = _build_decomposition_prompt(task_name, task_prompt, task_type, max_subtasks)

        # Add tool-aware enhancements
        tool_requirements = strategy.get("tool_requirements", [])
        strategy_type = strategy.get("type", "standard")

        tool_guidance = ""

        if ToolRequirement.INFORMATION_RETRIEVAL.value in tool_requirements:
            tool_guidance += """
工具增强指导 - 信息检索:
- 某些子任务可能需要搜索最新信息或外部资料
- 请确保包含信息收集和验证的子任务
- 考虑信息的时效性和可靠性要求
"""

        if ToolRequirement.DATA_PROCESSING.value in tool_requirements:
            tool_guidance += """
工具增强指导 - 数据处理:
- 某些子任务可能需要查询或分析结构化数据
- 请包含数据收集、清理和分析的子任务
- 考虑数据的格式转换和存储需求
"""

        if ToolRequirement.FILE_MANAGEMENT.value in tool_requirements:
            tool_guidance += """
工具增强指导 - 文件管理:
- 某些子任务可能需要读写文件或管理文档
- 请包含文件创建、编辑和组织的子任务
- 考虑文件格式和存储结构的规划
"""

        if tool_guidance:
            enhanced_prompt = f"{base_prompt}\n\n{tool_guidance}\n\n请在分解时考虑这些工具能力，确保子任务能够充分利用可用的外部工具。"
        else:
            enhanced_prompt = base_prompt

        return enhanced_prompt

    def _enhance_subtask_prompt(self, original_prompt: str, strategy: Dict[str, Any], index: int, task_id: int = None) -> str:
        """Enhance subtask prompt with Root Brief, parent chain, and tool-aware context"""
        
        # Phase 1: Inject Root Brief and Parent Chain at the top
        root_brief = ""
        parent_chain = ""
        
        if task_id:
            try:
                # Get root task
                root = self._find_root_task(task_id)
                if root:
                    root_name = root.get("name", "")
                    root_prompt = self.repo.get_task_input_prompt(root.get("id")) or ""
                    root_brief = f"[ROOT主题] {root_name}\n[核心目标] {root_prompt[:500]}\n\n"
                
                # Get parent chain
                parent = self.repo.get_parent(task_id)
                if parent:
                    parent_name = parent.get("name", "")
                    parent_chain = f"[父任务] {parent_name}\n\n"
            except Exception as e:
                logger.warning(f"Failed to inject root brief: {e}")
        
        # Phase 2: Add tool availability notice
        tool_requirements = strategy.get("tool_requirements", [])
        tool_notice = ""
        
        if tool_requirements and ToolRequirement.NONE.value not in tool_requirements:
            tool_notice = "\n\n[可用工具提示]\n"
            
            if ToolRequirement.INFORMATION_RETRIEVAL.value in tool_requirements:
                tool_notice += "- 如需最新信息或外部资料，可以请求搜索相关内容\n"
            
            if ToolRequirement.DATA_PROCESSING.value in tool_requirements:
                tool_notice += "- 如需结构化数据分析，可以请求查询相关数据库\n"
            
            if ToolRequirement.FILE_MANAGEMENT.value in tool_requirements:
                tool_notice += "- 如需文件操作，可以请求读写相关文件\n"
            
            tool_notice += "系统会自动识别需求并调用相应工具。"
        
        # Phase 3: Combine all parts with explicit theme constraint
        theme_constraint = "\n\n⚠️ 重要约束：所有内容必须紧扣上述ROOT主题，不得偏离。若信息不足，优先提问澄清。\n"
        
        return f"{root_brief}{parent_chain}{original_prompt}{theme_constraint}{tool_notice}"
    
    def _find_root_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Find root task by walking up parent chain"""
        try:
            current = self.repo.get_task_info(task_id)
            guard = 0
            while current and guard < 100:
                if current.get("task_type") == "root":
                    return current
                parent = self.repo.get_parent(current.get("id"))
                if not parent:
                    break
                current = parent
                guard += 1
        except Exception:
            pass
        return None


# Convenience functions for integration
async def decompose_task_with_tool_awareness(
    task_id: int, repo: Optional[TaskRepository] = None, max_subtasks: int = 8, force: bool = False
) -> Dict[str, Any]:
    """
    Decompose task with tool awareness

    This is the main entry point for tool-aware task decomposition.
    """
    decomposer = ToolAwareTaskDecomposer(repo)
    return await decomposer.decompose_with_tool_awareness(task_id, max_subtasks, force)


async def analyze_task_tool_requirements(task_id: int, repo: Optional[TaskRepository] = None) -> Dict[str, Any]:
    """Analyze tool requirements for a given task"""
    decomposer = ToolAwareTaskDecomposer(repo)
    await decomposer.initialize()

    task = decomposer.repo.get_task_info(task_id)
    if not task:
        return {"error": "Task not found"}

    task_name = task.get("name", "")
    task_prompt = decomposer.repo.get_task_input_prompt(task_id) or ""

    return await decomposer._analyze_task_tool_needs(task_name, task_prompt)


def should_use_tool_aware_decomposition(task: Dict[str, Any]) -> bool:
    """Determine if a task should use tool-aware decomposition"""

    # Check task complexity
    task_name = task.get("name", "")
    complexity = evaluate_task_complexity(task_name)

    # Check if task content suggests tool usage
    prompt_indicators = [
        "搜索",
        "查找",
        "最新",
        "数据",
        "分析",
        "文件",
        "报告",
        "search",
        "find",
        "latest",
        "data",
        "analysis",
        "file",
        "report",
    ]

    task_content = f"{task_name} {task.get('description', '')}".lower()
    has_tool_indicators = any(indicator in task_content for indicator in prompt_indicators)

    # Use tool-aware decomposition for complex tasks or tasks with tool indicators
    return complexity in ["medium", "high"] or has_tool_indicators
