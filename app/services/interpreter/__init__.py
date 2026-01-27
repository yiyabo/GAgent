"""
Result Interpreter Module

提供数据分析和结果解释功能，包含：
- DataProcessor: 数据集元数据提取
- TaskExecutor: 任务执行器（使用 Claude Code）
- PlanExecutorInterpreter: 计划执行器
- run_analysis: 一站式数据分析入口

重构说明：
- TaskExecutor 现在使用 Claude Code 作为执行后端
- CodeGenerator 和 LocalCodeInterpreter 不再被 TaskExecutor 直接使用
"""

from .metadata import DataProcessor, DatasetMetadata, ColumnMetadata
from .coder import CodeGenerator, CodeTaskResponse
from .local_interpreter import LocalCodeInterpreter, CodeExecutionResult
from .docker_interpreter import DockerCodeInterpreter
from .interpreter import run_analysis, run_analysis_async, execute_plan, AnalysisResult
from .plan_execute import PlanExecutorInterpreter, PlanExecutionResult, NodeExecutionStatus, NodeExecutionRecord
from .task_executer import TaskExecutor, TaskExecutionResult, TaskType, execute_task, execute_task_sync

__all__ = [
    # Metadata
    "DataProcessor",
    "DatasetMetadata",
    "ColumnMetadata",
    # Code Generation (保留兼容性)
    "CodeGenerator",
    "CodeTaskResponse",
    # Code Execution (保留兼容性)
    "LocalCodeInterpreter",
    "DockerCodeInterpreter",
    "CodeExecutionResult",
    # Main Entry
    "run_analysis",
    "run_analysis_async",
    "execute_plan",
    "AnalysisResult",
    # Plan Execution
    "PlanExecutorInterpreter",
    "PlanExecutionResult",
    "NodeExecutionStatus",
    "NodeExecutionRecord",
    # Task Execution
    "TaskExecutor",
    "TaskExecutionResult",
    "TaskType",
    "execute_task",
    "execute_task_sync",
]
