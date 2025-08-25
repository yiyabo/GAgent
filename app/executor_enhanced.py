"""
Backward Compatibility Wrapper for executor_enhanced.py

This file maintains the original API of executor_enhanced.py while delegating
to the new refactored execution modules. This ensures existing code continues
to work without modification.

The original 1088-line monolithic file has been split into:
- BaseTaskExecutor: Core execution logic
- EvaluationOrchestrator: Evaluation coordination
- LLMExecutionStrategy: LLM-based evaluation
- MultiExpertExecutionStrategy: Multi-expert evaluation
- AdversarialExecutionStrategy: Adversarial evaluation
- PromptBuilder: Prompt construction utilities
"""

# Import all the backward-compatible functions from the refactored module
from .executor_refactored import (
    execute_task,
    execute_task_with_evaluation,
    execute_task_with_llm_evaluation,
    execute_task_with_multi_expert_evaluation,
    execute_task_with_adversarial_evaluation,
    TaskExecutor,
    TaskExecutorFactory,
    default_executor
)

# Import the base utilities that might be used directly
from .execution.base_executor import BaseTaskExecutor

# For any legacy code that might import private functions, provide them
def _get_task_id_and_name(task):
    """Legacy function - use BaseTaskExecutor.get_task_id_and_name instead."""
    executor = BaseTaskExecutor()
    return executor.get_task_id_and_name(task)

def _fetch_prompt(task_id, default_prompt, repo):
    """Legacy function - use BaseTaskExecutor.fetch_prompt instead."""
    executor = BaseTaskExecutor(repo)
    return executor.fetch_prompt(task_id, default_prompt)

def _glm_chat(prompt: str) -> str:
    """Legacy function - use BaseTaskExecutor.execute_llm_chat instead."""
    executor = BaseTaskExecutor()
    return executor.execute_llm_chat(prompt)

def _generate_task_embedding_async(task_id: int, content: str, repo):
    """Legacy function - use BaseTaskExecutor.generate_task_embedding_async instead."""
    executor = BaseTaskExecutor(repo)
    return executor.generate_task_embedding_async(task_id, content)

# Legacy imports that might be expected
import logging
from typing import Any, Dict, List, Optional
from .interfaces import TaskRepository
from .models import EvaluationConfig, EvaluationResult, TaskExecutionResult
from .repository.tasks import default_repo

logger = logging.getLogger(__name__)

# Re-export everything from the new modules for backward compatibility
__all__ = [
    'execute_task',
    'execute_task_with_evaluation', 
    'execute_task_with_llm_evaluation',
    'execute_task_with_multi_expert_evaluation',
    'execute_task_with_adversarial_evaluation',
    'TaskExecutor',
    'TaskExecutorFactory',
    'default_executor',
    '_get_task_id_and_name',
    '_fetch_prompt', 
    '_glm_chat',
    '_generate_task_embedding_async'
]