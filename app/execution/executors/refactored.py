"""
Refactored Task Executor

This is the new main entry point for task execution, replacing executor_enhanced.py.
Provides a clean interface to the refactored execution strategies while maintaining
backward compatibility with existing code.
"""

import logging
from typing import Any, Dict, List, Optional

from .interfaces import TaskRepository
from .models import EvaluationConfig, TaskExecutionResult
from .repository.tasks import default_repo

# Import the new execution components
from .execution.base_executor import BaseTaskExecutor
from .execution.evaluation_orchestrator import EvaluationOrchestrator
from .execution.llm_execution_strategy import LLMExecutionStrategy
from .execution.multi_expert_execution_strategy import MultiExpertExecutionStrategy
from .execution.adversarial_execution_strategy import AdversarialExecutionStrategy

logger = logging.getLogger(__name__)


class TaskExecutorFactory:
    """Factory for creating different types of task executors."""
    
    @staticmethod
    def create_base_executor(repo: Optional[TaskRepository] = None) -> BaseTaskExecutor:
        """Create a basic task executor."""
        return BaseTaskExecutor(repo)
    
    @staticmethod
    def create_evaluation_orchestrator(repo: Optional[TaskRepository] = None) -> EvaluationOrchestrator:
        """Create an evaluation-driven executor."""
        return EvaluationOrchestrator(repo)
    
    @staticmethod
    def create_llm_executor(repo: Optional[TaskRepository] = None) -> LLMExecutionStrategy:
        """Create an LLM evaluation executor."""
        return LLMExecutionStrategy(repo)
    
    @staticmethod
    def create_multi_expert_executor(repo: Optional[TaskRepository] = None) -> MultiExpertExecutionStrategy:
        """Create a multi-expert evaluation executor."""
        return MultiExpertExecutionStrategy(repo)
    
    @staticmethod
    def create_adversarial_executor(repo: Optional[TaskRepository] = None) -> AdversarialExecutionStrategy:
        """Create an adversarial evaluation executor."""
        return AdversarialExecutionStrategy(repo)


# Backward compatibility functions - these maintain the original API
def execute_task(
    task,
    repo: Optional[TaskRepository] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult:
    """
    Execute task using legacy single-pass method.
    Maintained for backward compatibility.
    """
    executor = TaskExecutorFactory.create_base_executor(repo)
    return executor.execute_legacy_task(task, use_context, context_options)


def execute_task_with_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult:
    """
    Execute task with iterative evaluation and improvement.
    Maintained for backward compatibility.
    """
    orchestrator = TaskExecutorFactory.create_evaluation_orchestrator(repo)
    return orchestrator.execute_with_evaluation(
        task=task,
        max_iterations=max_iterations,
        quality_threshold=quality_threshold,
        evaluation_config=evaluation_config,
        use_context=use_context,
        context_options=context_options
    )


def execute_task_with_llm_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
    evaluation_config: Optional[EvaluationConfig] = None,
) -> TaskExecutionResult:
    """
    Execute task with LLM-based intelligent evaluation.
    Maintained for backward compatibility.
    """
    executor = TaskExecutorFactory.create_llm_executor(repo)
    return executor.execute(
        task=task,
        max_iterations=max_iterations,
        quality_threshold=quality_threshold,
        use_context=use_context,
        context_options=context_options,
        evaluation_config=evaluation_config
    )


def execute_task_with_multi_expert_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    experts: Optional[List[str]] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
    evaluation_config: Optional[EvaluationConfig] = None,
) -> TaskExecutionResult:
    """
    Execute task with multi-expert evaluation.
    Maintained for backward compatibility.
    """
    executor = TaskExecutorFactory.create_multi_expert_executor(repo)
    return executor.execute(
        task=task,
        max_iterations=max_iterations,
        quality_threshold=quality_threshold,
        experts=experts,
        use_context=use_context,
        context_options=context_options,
        evaluation_config=evaluation_config
    )


def execute_task_with_adversarial_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    max_rounds: int = 3,
    quality_threshold: float = 0.8,
    improvement_threshold: float = 0.1,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
    evaluation_config: Optional[EvaluationConfig] = None,
) -> TaskExecutionResult:
    """
    Execute task with adversarial evaluation.
    Maintained for backward compatibility.
    """
    executor = TaskExecutorFactory.create_adversarial_executor(repo)
    return executor.execute(
        task=task,
        max_iterations=max_iterations,
        max_rounds=max_rounds,
        quality_threshold=quality_threshold,
        improvement_threshold=improvement_threshold,
        use_context=use_context,
        context_options=context_options,
        evaluation_config=evaluation_config
    )


# New improved interfaces for direct use
class TaskExecutor:
    """
    Main task executor class with improved interface.
    
    This provides a cleaner, more maintainable interface compared to the
    function-based approach in the original executor_enhanced.py.
    """
    
    def __init__(self, repo: Optional[TaskRepository] = None):
        self.repo = repo or default_repo
        self._factory = TaskExecutorFactory()
    
    def execute_basic(
        self, 
        task, 
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """Execute task with basic single-pass execution."""
        executor = self._factory.create_base_executor(self.repo)
        return executor.execute_legacy_task(task, use_context, context_options)
    
    def execute_with_evaluation(
        self,
        task,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        evaluation_config: Optional[EvaluationConfig] = None,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """Execute task with general evaluation-driven improvement."""
        orchestrator = self._factory.create_evaluation_orchestrator(self.repo)
        return orchestrator.execute_with_evaluation(
            task=task,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            evaluation_config=evaluation_config,
            use_context=use_context,
            context_options=context_options
        )
    
    def execute_with_llm_evaluation(
        self,
        task,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """Execute task with LLM-based intelligent evaluation."""
        executor = self._factory.create_llm_executor(self.repo)
        return executor.execute(
            task=task,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=context_options
        )
    
    def execute_with_expert_evaluation(
        self,
        task,
        experts: Optional[List[str]] = None,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """Execute task with multi-expert evaluation."""
        executor = self._factory.create_multi_expert_executor(self.repo)
        return executor.execute(
            task=task,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            experts=experts,
            use_context=use_context,
            context_options=context_options
        )
    
    def execute_with_adversarial_evaluation(
        self,
        task,
        max_iterations: int = 3,
        max_rounds: int = 3,
        quality_threshold: float = 0.8,
        improvement_threshold: float = 0.1,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """Execute task with adversarial evaluation."""
        executor = self._factory.create_adversarial_executor(self.repo)
        return executor.execute(
            task=task,
            max_iterations=max_iterations,
            max_rounds=max_rounds,
            quality_threshold=quality_threshold,
            improvement_threshold=improvement_threshold,
            use_context=use_context,
            context_options=context_options
        )


# Global instance for convenience
default_executor = TaskExecutor()