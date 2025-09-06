"""
Adversarial Execution Strategy

Specialized execution strategy using adversarial evaluation (Generator vs Critic).
Extracted from executor_enhanced.py to separate adversarial evaluation logic.
"""

import logging
import time
from typing import Any, Dict, Optional

from ..interfaces import TaskRepository
from ..models import EvaluationConfig, TaskExecutionResult
from ..services.evaluation.adversarial_evaluator import get_adversarial_evaluator
from ..services.evaluation.evaluation_supervisor import monitor_evaluation
from .base_executor import BaseTaskExecutor
from .prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


class AdversarialExecutionStrategy:
    """Execution strategy using adversarial evaluation system (Generator vs Critic)."""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.base_executor = BaseTaskExecutor(repo)
        self.prompt_builder = PromptBuilder(self.base_executor.repo)
        self.repo = self.base_executor.repo
        self.adversarial_evaluator = get_adversarial_evaluator()

    def execute(
        self,
        task,
        max_iterations: int = 3,
        max_rounds: int = 3,
        quality_threshold: float = 0.8,
        improvement_threshold: float = 0.1,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None,
        evaluation_config: Optional[EvaluationConfig] = None,
    ) -> TaskExecutionResult:
        """
        Execute task with adversarial evaluation and iterative improvement.

        This strategy uses a Generator vs Critic approach inspired by GANs:
        - Generator creates/improves content
        - Critic finds flaws and provides feedback
        - Iterative adversarial loop until convergence
        """
        start_time = time.time()
        task_id, name = self.base_executor.get_task_id_and_name(task)

        logger.info(f"Starting adversarial evaluation execution for task {task_id}: {name}")

        # Build task context for evaluation
        task_context = self.base_executor.build_task_context(task)
        task_context.update(
            {
                "evaluation_mode": "adversarial",
                "quality_threshold": quality_threshold,
                "max_iterations": max_iterations,
                "max_rounds": max_rounds,
                "improvement_threshold": improvement_threshold,
            }
        )

        # Prepare initial prompt
        default_prompt = self._build_adversarial_prompt(name)
        base_prompt = self.base_executor.fetch_prompt(task_id, default_prompt)

        # Build contextual prompt if requested
        initial_prompt = self.prompt_builder.build_context_prompt(
            task_id, name, base_prompt, use_context, context_options
        )

        # Execute adversarial iterative improvement
        result = self._execute_adversarial_iterative_loop(
            task_id,
            name,
            initial_prompt,
            task_context,
            max_iterations,
            max_rounds,
            quality_threshold,
            improvement_threshold,
        )

        # Store final results
        self._finalize_task_execution(task_id, result)

        result.execution_time = time.time() - start_time

        # Monitor evaluation performance
        self._monitor_evaluation_performance(task_id, result)

        return result

    def _execute_adversarial_iterative_loop(
        self,
        task_id: int,
        task_name: str,
        initial_prompt: str,
        task_context: Dict[str, Any],
        max_iterations: int,
        max_rounds: int,
        quality_threshold: float,
        improvement_threshold: float,
    ) -> TaskExecutionResult:
        """Execute the adversarial evaluation iterative loop."""
        best_content = ""
        best_evaluation = None
        current_prompt = initial_prompt

        for iteration in range(max_iterations):
            try:
                logger.info(f"Adversarial evaluation - Task {task_id} iteration {iteration + 1}/{max_iterations}")

                # Generate initial content for this iteration
                content = self.base_executor.execute_llm_chat(current_prompt)

                # Run adversarial evaluation with multiple rounds
                evaluation = self.adversarial_evaluator.adversarial_evaluate(
                    content=content,
                    task_context=task_context,
                    max_rounds=max_rounds,
                    improvement_threshold=improvement_threshold,
                    iteration=iteration,
                )

                # Store evaluation history
                self._store_evaluation_history(task_id, iteration, evaluation.content or content, evaluation)

                logger.info(
                    f"Task {task_id} iteration {iteration + 1} adversarial score: {evaluation.overall_score:.3f}"
                )

                # Log adversarial details if available
                if evaluation.metadata:
                    rounds_completed = evaluation.metadata.get("rounds_completed", 0)
                    final_criticism = evaluation.metadata.get("final_criticism", "None")
                    logger.debug(f"Adversarial rounds completed: {rounds_completed}")
                    if final_criticism and final_criticism != "None":
                        logger.debug(f"Final criticism: {final_criticism[:100]}...")

                # Use the improved content from adversarial process
                final_content = evaluation.content or content

                # Check quality threshold
                if evaluation.overall_score >= quality_threshold:
                    logger.info(
                        f"Task {task_id} reached adversarial evaluation threshold: {evaluation.overall_score:.3f}"
                    )
                    return TaskExecutionResult(
                        task_id=task_id,
                        status="done",
                        content=final_content,
                        evaluation=evaluation,
                        iterations=iteration + 1,
                    )

                # Track best result
                if best_evaluation is None or evaluation.overall_score > best_evaluation.overall_score:
                    best_content = final_content
                    best_evaluation = evaluation

                # Build adversarial-informed revision prompt for next iteration
                if iteration < max_iterations - 1:
                    current_prompt = self._build_adversarial_revision_prompt(
                        initial_prompt, final_content, evaluation, iteration
                    )

            except Exception as e:
                logger.error(f"Adversarial evaluation iteration {iteration + 1} failed for task {task_id}: {e}")
                continue

        # Return best result
        final_status = "done" if best_content else "failed"
        if best_evaluation:
            logger.warning(f"Task {task_id} completed with best adversarial score: {best_evaluation.overall_score:.3f}")

        return TaskExecutionResult(
            task_id=task_id,
            status=final_status,
            content=best_content,
            evaluation=best_evaluation,
            iterations=max_iterations,
        )

    def _build_adversarial_prompt(self, task_name: str) -> str:
        """Build prompt optimized for adversarial evaluation."""
        return f"""Complete the following task with the highest possible quality, as your work will undergo rigorous adversarial evaluation:

Task: {task_name}

Your content will be evaluated by an adversarial system where:
- A Generator (you) creates content
- A Critic will rigorously examine your work for flaws
- Multiple rounds of improvement will occur based on criticism
- Only the highest quality content will pass evaluation

Requirements:
- Write approximately 200-300 words
- Anticipate potential criticisms and address them preemptively
- Use precise, accurate language
- Structure content logically and clearly
- Include specific evidence or examples where appropriate
- Ensure completeness and thoroughness
- Maintain professional tone throughout

Create content that can withstand adversarial scrutiny:"""

    def _build_adversarial_revision_prompt(
        self, original_prompt: str, previous_content: str, evaluation, iteration: int
    ) -> str:
        """Build revision prompt incorporating adversarial feedback."""
        # Extract adversarial-specific feedback
        adversarial_rounds = evaluation.metadata.get("rounds_completed", 0) if evaluation.metadata else 0
        final_criticism = evaluation.metadata.get("final_criticism", "") if evaluation.metadata else ""
        robustness_score = evaluation.metadata.get("robustness_score", 0.0) if evaluation.metadata else 0.0

        revision_prompt = f"""Your previous response underwent adversarial evaluation and needs improvement.

ORIGINAL TASK:
{original_prompt}

PREVIOUS RESPONSE (after {adversarial_rounds} adversarial rounds):
{previous_content}

ADVERSARIAL EVALUATION RESULTS (Score: {evaluation.overall_score:.2f}, Robustness: {robustness_score:.2f}):

FINAL CRITICISM FROM ADVERSARIAL SYSTEM:
{final_criticism}

IMPROVEMENT SUGGESTIONS:
{chr(10).join([f"• {s}" for s in evaluation.suggestions])}

ADVERSARIAL REVISION REQUIREMENTS (Attempt {iteration + 1}):
- Address ALL criticisms identified by the adversarial system
- Strengthen areas where robustness was questioned
- Anticipate and preemptively address potential future criticisms
- Improve overall quality to exceed the adversarial threshold
- Make content more resistant to critical examination
- Maintain accuracy while enhancing persuasiveness

Please provide a significantly improved version that can withstand even more rigorous adversarial evaluation:"""

        return revision_prompt

    def _finalize_task_execution(self, task_id: int, result: TaskExecutionResult):
        """Store final execution results."""
        try:
            if result.status == "done" and result.content:
                self.repo.upsert_task_output(task_id, result.content)
                self.repo.update_task_status(task_id, "done")
                # Generate embedding asynchronously
                self.base_executor.generate_task_embedding_async(task_id, result.content)
                logger.info(f"Task {task_id} completed successfully with adversarial evaluation")
            else:
                self.repo.update_task_status(task_id, "failed")
                logger.warning(f"Task {task_id} failed to meet adversarial evaluation standards")
        except Exception as e:
            logger.error(f"Failed to finalize task {task_id}: {e}")

    def _store_evaluation_history(self, task_id: int, iteration: int, content: str, evaluation):
        """Store adversarial evaluation history."""
        try:
            # Extract adversarial-specific metadata
            adversarial_metadata = {
                "evaluation_method": "adversarial",
                "generator_critic_rounds": evaluation.metadata.get("rounds_completed", 0) if evaluation.metadata else 0,
                "robustness_score": evaluation.metadata.get("robustness_score", 0.0) if evaluation.metadata else 0.0,
                "criticism_severity": (
                    evaluation.metadata.get("criticism_severity", "low") if evaluation.metadata else "low"
                ),
                "convergence_achieved": evaluation.metadata.get("converged", False) if evaluation.metadata else False,
                **(evaluation.metadata or {}),
            }

            self.repo.store_evaluation_history(
                task_id=task_id,
                iteration=iteration,
                content=content,
                overall_score=evaluation.overall_score,
                dimension_scores=evaluation.dimensions.__dict__ if evaluation.dimensions else {},
                suggestions=evaluation.suggestions,
                needs_revision=evaluation.needs_revision,
                metadata=adversarial_metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to store adversarial evaluation history: {e}")

    def _monitor_evaluation_performance(self, task_id: int, result: TaskExecutionResult):
        """Monitor adversarial evaluation system performance."""
        try:
            # Calculate additional adversarial metrics
            total_rounds = 0
            if result.evaluation and result.evaluation.metadata:
                total_rounds = result.evaluation.metadata.get("rounds_completed", 0) * result.iterations

            monitor_evaluation(
                task_id=task_id,
                evaluation_method="adversarial",
                final_score=result.evaluation.overall_score if result.evaluation else 0.0,
                iterations_used=result.iterations,
                execution_time=result.execution_time,
                success=(result.status == "done"),
                additional_metrics={
                    "total_adversarial_rounds": total_rounds,
                    "convergence_rate": (
                        result.evaluation.metadata.get("converged", False)
                        if result.evaluation and result.evaluation.metadata
                        else False
                    ),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to monitor adversarial evaluation performance: {e}")
