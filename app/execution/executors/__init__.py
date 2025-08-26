"""
Task execution system.

This module provides different executor implementations for task execution:
- BaseExecutor: Core task execution functionality
"""

from .base import execute_task

__all__ = [
    'execute_task'
]
