"""
LLM Execution Strategy

Specialized execution strategy using LLM-based intelligent evaluation.
Extracted from executor_enhanced.py to separate LLM evaluation logic.
"""

import logging
import time
from typing import Any, Dict, Optional

from ..interfaces import TaskRepository
from ..models import EvaluationConfig, TaskExecutionResult
from ..services.evaluation.evaluation_supervisor import (
    get_evaluation_supervisor,
    monitor_evaluation,
)
from ..services.evaluation.llm_evaluator import get_llm_evaluator
from .base_executor import BaseTaskExecutor
from .prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


class LLMExecutionStrategy:
    """Execution strategy using LLM-based intelligent evaluation."""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.base_executor = BaseTaskExecutor(repo)
        self.prompt_builder = PromptBuilder(self.base_executor.repo)
        self.repo = self.base_executor.repo
        self.llm_evaluator = get_llm_evaluator()
        self.supervisor = get_evaluation_supervisor()

    def execute(
        self,
        task,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None,
        evaluation_config: Optional[EvaluationConfig] = None,
    ) -> TaskExecutionResult:
        """
        Execute task with LLM-based intelligent evaluation and iterative improvement.

        This strategy uses advanced LLM evaluation instead of rule-based evaluation,
        providing more nuanced feedback and better content improvement.
        """
        start_time = time.time()
        task_id, name = self.base_executor.get_task_id_and_name(task)

        logger.info(f"Starting LLM evaluation execution for task {task_id}: {name}")

        # Build task context for evaluation
        task_context = self.base_executor.build_task_context(task)
        task_context.update(
            {
                "evaluation_mode": "llm_intelligent",
                "quality_threshold": quality_threshold,
                "max_iterations": max_iterations,
            }
        )

        # Prepare prompts
        default_prompt = self._build_default_prompt(name)
        base_prompt = self.base_executor.fetch_prompt(task_id, default_prompt)

        # Build contextual prompt if requested
        current_prompt = self.prompt_builder.build_context_prompt(
            task_id, name, base_prompt, use_context, context_options
        )

        # Execute iterative improvement with LLM evaluation
        result = self._execute_llm_iterative_loop(
            task_id, name, current_prompt, task_context, max_iterations, quality_threshold
        )

        # Store final results
        self._finalize_task_execution(task_id, result)

        result.execution_time = time.time() - start_time

        # Monitor evaluation performance
        self._monitor_evaluation_performance(task_id, result)

        return result

    def _execute_llm_iterative_loop(
        self,
        task_id: int,
        task_name: str,
        initial_prompt: str,
        task_context: Dict[str, Any],
        max_iterations: int,
        quality_threshold: float,
    ) -> TaskExecutionResult:
        """Execute the LLM evaluation iterative loop."""
        best_content = ""
        best_evaluation = None
        current_prompt = initial_prompt

        for iteration in range(max_iterations):
            try:
                logger.info(f"LLM evaluation - Task {task_id} iteration {iteration + 1}/{max_iterations}")

                # Generate content
                content = self.base_executor.execute_llm_chat(current_prompt)

                # LLM-based intelligent evaluation
                evaluation = self.llm_evaluator.evaluate_content_intelligent(
                    content=content, task_context=task_context, iteration=iteration
                )

                # Store evaluation history
                self._store_evaluation_history(task_id, iteration, content, evaluation)

                logger.info(f"Task {task_id} iteration {iteration + 1} score: {evaluation.overall_score:.3f}")

                # Check quality threshold
                if evaluation.overall_score >= quality_threshold:
                    logger.info(f"Task {task_id} reached LLM evaluation threshold: {evaluation.overall_score:.3f}")
                    return TaskExecutionResult(
                        task_id=task_id, status="done", content=content, evaluation=evaluation, iterations=iteration + 1
                    )

                # Track best result
                if best_evaluation is None or evaluation.overall_score > best_evaluation.overall_score:
                    best_content = content
                    best_evaluation = evaluation

                # Build LLM-specific revision prompt
                if iteration < max_iterations - 1:
                    current_prompt = self.prompt_builder.build_llm_revision_prompt(
                        initial_prompt, content, evaluation, iteration
                    )

            except Exception as e:
                logger.error(f"LLM evaluation iteration {iteration + 1} failed for task {task_id}: {e}")

                # Try fallback evaluation if LLM evaluation fails
                try:
                    fallback_evaluation = self.llm_evaluator._fallback_evaluation(content, task_context)
                    if fallback_evaluation.overall_score > (best_evaluation.overall_score if best_evaluation else 0):
                        best_content = content
                        best_evaluation = fallback_evaluation
                except Exception as fallback_error:
                    logger.error(f"Fallback evaluation also failed: {fallback_error}")
                continue

        # Return best result
        final_status = "done" if best_content else "failed"
        if best_evaluation:
            logger.warning(f"Task {task_id} completed with best LLM score: {best_evaluation.overall_score:.3f}")

        return TaskExecutionResult(
            task_id=task_id,
            status=final_status,
            content=best_content,
            evaluation=best_evaluation,
            iterations=max_iterations,
        )

    def _build_default_prompt(self, task_name: str) -> str:
        """Build default prompt optimized for LLM evaluation."""
        return f"""Please complete the following task with high quality and attention to detail:

Task: {task_name}

Requirements:
- Write approximately 200 words
- Use clear, professional language
- Ensure content is relevant, complete, and accurate
- Structure your response logically
- Include specific details and examples where appropriate
- Maintain scientific rigor if applicable

Your response will be evaluated on multiple dimensions including relevance, completeness, accuracy, clarity, coherence, and scientific rigor. Please provide your best work:"""

    def _finalize_task_execution(self, task_id: int, result: TaskExecutionResult):
        """Store final execution results."""
        try:
            if result.status == "done" and result.content:
                self.repo.store_task_output(task_id, result.content)
                self.repo.update_task_status(task_id, "done")
                # Generate embedding asynchronously
                self.base_executor.generate_task_embedding_async(task_id, result.content)
                logger.info(f"Task {task_id} completed successfully with LLM evaluation")
            else:
                self.repo.update_task_status(task_id, "failed")
                logger.warning(f"Task {task_id} failed to meet LLM evaluation standards")
        except Exception as e:
            logger.error(f"Failed to finalize task {task_id}: {e}")

    def _store_evaluation_history(self, task_id: int, iteration: int, content: str, evaluation):
        """Store LLM evaluation history."""
        try:
            self.repo.store_evaluation_result(
                task_id=task_id,
                iteration=iteration,
                content=content,
                overall_score=evaluation.overall_score,
                dimension_scores=evaluation.dimensions,
                suggestions=evaluation.suggestions,
                needs_revision=evaluation.needs_revision,
                metadata={
                    "evaluation_method": "llm_intelligent",
                    "model_used": "glm-4-plus",
                    **(evaluation.metadata or {}),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to store LLM evaluation history: {e}")

    def _monitor_evaluation_performance(self, task_id: int, result: TaskExecutionResult):
        """Monitor LLM evaluation system performance."""
        try:
            monitor_evaluation(
                task_id=task_id,
                evaluation_method="llm_intelligent",
                final_score=result.evaluation.overall_score if result.evaluation else 0.0,
                iterations_used=result.iterations,
                execution_time=result.execution_time,
                success=(result.status == "done"),
            )
        except Exception as e:
            logger.warning(f"Failed to monitor evaluation performance: {e}")
