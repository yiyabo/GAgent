"""
Result Interpreter Module

analysisresult, : 
- DataProcessor: 
- TaskExecutor: taskexecute( Claude Code)
- PlanExecutorInterpreter: plan execution
- run_analysis: analysis

description: 
- TaskExecutor  Claude Code executebackend
- CodeGenerator  LocalCodeInterpreter  TaskExecutor 
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
    "CodeGenerator",
    "CodeTaskResponse",
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
