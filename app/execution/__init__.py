"""
Enhanced Task Execution Module

This module provides various execution strategies for tasks with evaluation and iterative improvement.
Split from the original monolithic executor_enhanced.py into focused components.
"""

from .adversarial_execution_strategy import AdversarialExecutionStrategy
from .atomic_executor import AtomicExecutor, execute_atomic_task
from .assemblers import CompositeAssembler, RootAssembler
from .base_executor import BaseTaskExecutor
from .evaluation_orchestrator import EvaluationOrchestrator
from .llm_execution_strategy import LLMExecutionStrategy
from .multi_expert_execution_strategy import MultiExpertExecutionStrategy
from .prompt_builder import PromptBuilder

__all__ = [
    "BaseTaskExecutor",
    "EvaluationOrchestrator",
    "LLMExecutionStrategy",
    "MultiExpertExecutionStrategy",
    "AdversarialExecutionStrategy",
    "PromptBuilder",
    "AtomicExecutor",
    "CompositeAssembler",
    "RootAssembler",
    "execute_atomic_task",
]
