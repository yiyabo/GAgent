"""
Task execution system.

This module provides different executor implementations for task execution:
- BaseExecutor: Core task execution functionality
- ToolEnhancedExecutor: Tool-enhanced execution with external capabilities
"""

from .base import execute_task as base_execute_task
from .tool_enhanced import execute_task_enhanced

# Use tool-enhanced execution by default for intelligent agent capabilities
execute_task = execute_task_enhanced

__all__ = ["execute_task", "base_execute_task", "execute_task_enhanced"]
