"""
Evaluation Orchestrator

Coordinates different evaluation strategies and manages iterative improvement loops.
Extracted from executor_enhanced.py to separate evaluation coordination logic.
"""

import logging
import time
from typing import Any, Dict, Optional

from ..interfaces import TaskRepository
from ..models import EvaluationConfig, EvaluationResult, TaskExecutionResult
from ..services.evaluation.content_evaluator import get_evaluator
from .base_executor import BaseTaskExecutor
from .prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


class EvaluationOrchestrator:
    """Orchestrates task execution with evaluation-driven iterative improvement."""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.base_executor = BaseTaskExecutor(repo)
        self.prompt_builder = PromptBuilder(self.base_executor.repo)
        self.repo = self.base_executor.repo

    def execute_with_evaluation(
        self,
        task,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        evaluation_config: Optional[EvaluationConfig] = None,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None,
    ) -> TaskExecutionResult:
        """
        Execute task with iterative evaluation and improvement.

        This is the main entry point for evaluation-driven execution.
        """
        start_time = time.time()
        task_id, name = self.base_executor.get_task_id_and_name(task)

        # Setup evaluation configuration
        config = evaluation_config or EvaluationConfig(
            quality_threshold=quality_threshold, max_iterations=max_iterations
        )
        evaluator = get_evaluator(config)

        # Store evaluation config for this task
        self._store_evaluation_config(task_id, config)

        # Build task context for evaluation
        task_context = self.base_executor.build_task_context(task)

        # Prepare initial prompt
        default_prompt = self._build_default_prompt(name)
        base_prompt = self.base_executor.fetch_prompt(task_id, default_prompt)

        # Build contextual prompt if requested
        prompt = self.prompt_builder.build_context_prompt(task_id, name, base_prompt, use_context, context_options)

        # Execute iterative improvement loop
        result = self._execute_iterative_loop(task_id, name, prompt, task_context, evaluator, config)

        # Store final results
        if result.status == "done" and result.content:
            self.repo.upsert_task_output(task_id, result.content)
            self.repo.update_task_status(task_id, "done")
            # Generate embedding asynchronously
            self.base_executor.generate_task_embedding_async(task_id, result.content)
        else:
            self.repo.update_task_status(task_id, "failed")

        result.execution_time = time.time() - start_time
        return result

    def _execute_iterative_loop(
        self,
        task_id: int,
        task_name: str,
        initial_prompt: str,
        task_context: Dict[str, Any],
        evaluator,
        config: EvaluationConfig,
    ) -> TaskExecutionResult:
        """Execute the iterative improvement loop."""
        best_content = ""
        best_evaluation = None
        current_prompt = initial_prompt

        for iteration in range(config.max_iterations):
            try:
                logger.info(f"Task {task_id} iteration {iteration + 1}/{config.max_iterations}")

                # Generate content
                content = self.base_executor.execute_llm_chat(current_prompt)

                # Evaluate content
                evaluation = evaluator.evaluate_content(content=content, task_context=task_context, iteration=iteration)

                # Store evaluation history
                self._store_evaluation_history(task_id, iteration, content, evaluation)

                # Check if quality threshold is met
                if evaluation.overall_score >= config.quality_threshold:
                    logger.info(f"Task {task_id} reached quality threshold: {evaluation.overall_score:.3f}")
                    return TaskExecutionResult(
                        task_id=task_id, status="done", content=content, evaluation=evaluation, iterations=iteration + 1
                    )

                # Update best result if this is better
                if best_evaluation is None or evaluation.overall_score > best_evaluation.overall_score:
                    best_content = content
                    best_evaluation = evaluation

                # Build revision prompt for next iteration
                if iteration < config.max_iterations - 1:
                    current_prompt = self.prompt_builder.build_revision_prompt(
                        initial_prompt, content, evaluation, iteration
                    )

            except Exception as e:
                logger.error(f"Iteration {iteration + 1} failed for task {task_id}: {e}")
                continue

        # Return best result if no iteration met the threshold
        if best_evaluation:
            logger.warning(
                f"Task {task_id} did not meet quality threshold. Best score: {best_evaluation.overall_score:.3f}"
            )
        else:
            logger.error(f"Task {task_id} failed completely - no successful iterations")

        return TaskExecutionResult(
            task_id=task_id,
            status="done" if best_content else "failed",
            content=best_content,
            evaluation=best_evaluation,
            iterations=config.max_iterations,
        )

    def _build_default_prompt(self, task_name: str) -> str:
        """Build default prompt for a task."""
        return (
            f"Write a concise, clear section that fulfills the following task.\\n"
            f"Task: {task_name}.\\n"
            f"Length: ~200 words. Use a neutral, professional tone. "
            f"Avoid domain-specific assumptions unless explicitly provided."
        )

    def _store_evaluation_config(self, task_id: int, config: EvaluationConfig):
        """Store evaluation configuration for the task."""
        try:
            self.repo.store_evaluation_config(
                task_id=task_id,
                quality_threshold=config.quality_threshold,
                max_iterations=config.max_iterations,
                evaluation_dimensions=config.evaluation_dimensions,
                domain_specific=config.domain_specific,
                strict_mode=config.strict_mode,
                custom_weights=config.custom_weights,
            )
        except Exception as e:
            logger.warning(f"Failed to store evaluation config for task {task_id}: {e}")

    def _store_evaluation_history(self, task_id: int, iteration: int, content: str, evaluation: EvaluationResult):
        """Store evaluation history for debugging and analysis."""
        try:
            self.repo.store_evaluation_history(
                task_id=task_id,
                iteration=iteration,
                content=content,
                overall_score=evaluation.overall_score,
                dimension_scores=evaluation.dimensions.__dict__ if evaluation.dimensions else {},
                suggestions=evaluation.suggestions,
                needs_revision=evaluation.needs_revision,
                metadata=evaluation.metadata or {},
            )
        except Exception as e:
            logger.warning(f"Failed to store evaluation history for task {task_id}, iteration {iteration}: {e}")
