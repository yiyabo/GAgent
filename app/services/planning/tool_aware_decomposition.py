"""
Tool-Aware Task Decomposition

This module provides tool-aware task decomposition capabilities that consider
available tool capabilities when breaking down complex tasks.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from .recursive_decomposition import (
    MAX_DECOMPOSITION_DEPTH,
    TaskType,
    _build_decomposition_prompt,
    determine_task_type,
    evaluate_task_complexity,
)
from app.services.planning.planning import propose_plan_service

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

        # Use planning service for decomposition

        plan_payload = {
            "goal": enhanced_prompt,
            "title": f"工具增强_分解_{task_name}",
            "sections": strategy["suggested_subtasks"],
        }

        plan_result = propose_plan_service(plan_payload)

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

            # Create subtask
            subtask_id = self.repo.create_task(
                name=subtask_name, status="pending", priority=subtask_priority, parent_id=task_id, task_type=child_type
            )

            # Enhance subtask prompt with tool context
            original_prompt = subtask.get("prompt", "")
            enhanced_subtask_prompt = self._enhance_subtask_prompt(original_prompt, strategy, i)

            if enhanced_subtask_prompt:
                self.repo.upsert_task_input(subtask_id, enhanced_subtask_prompt)

            created_subtasks.append(
                {
                    "id": subtask_id,
                    "name": subtask_name,
                    "type": child_type,
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

    def _enhance_subtask_prompt(self, original_prompt: str, strategy: Dict[str, Any], index: int) -> str:
        """Enhance subtask prompt with tool-aware context"""

        tool_requirements = strategy.get("tool_requirements", [])

        if not tool_requirements or ToolRequirement.NONE.value in tool_requirements:
            return original_prompt

        # Add tool availability notice
        tool_notice = "\n\n[可用工具提示]\n"

        if ToolRequirement.INFORMATION_RETRIEVAL.value in tool_requirements:
            tool_notice += "- 如需最新信息或外部资料，可以请求搜索相关内容\n"

        if ToolRequirement.DATA_PROCESSING.value in tool_requirements:
            tool_notice += "- 如需结构化数据分析，可以请求查询相关数据库\n"

        if ToolRequirement.FILE_MANAGEMENT.value in tool_requirements:
            tool_notice += "- 如需文件操作，可以请求读写相关文件\n"

        tool_notice += "系统会自动识别需求并调用相应工具。"

        return f"{original_prompt}{tool_notice}"


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
