"""
Multi-Expert Execution Strategy

Specialized execution strategy using multi-expert evaluation with domain expertise.
Extracted from executor_enhanced.py to separate multi-expert evaluation logic.
"""

import logging
import time
from typing import Any, Dict, Optional

from ..interfaces import TaskRepository
from ..models import EvaluationConfig, TaskExecutionResult
from ..services.evaluation.evaluation_supervisor import monitor_evaluation
from ..services.evaluation.expert_evaluator import get_multi_expert_evaluator
from .base_executor import BaseTaskExecutor
from .prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)


class MultiExpertExecutionStrategy:
    """Execution strategy using multi-expert evaluation system."""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.base_executor = BaseTaskExecutor(repo)
        self.prompt_builder = PromptBuilder(self.base_executor.repo)
        self.repo = self.base_executor.repo
        self.expert_evaluator = get_multi_expert_evaluator()

    def execute(
        self,
        task,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        experts: Optional[list] = None,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None,
        evaluation_config: Optional[EvaluationConfig] = None,
    ) -> TaskExecutionResult:
        """
        Execute task with multi-expert evaluation and iterative improvement.

        This strategy simulates evaluation by multiple domain experts:
        - Theoretical Biologist
        - Clinical Physician
        - Regulatory Expert
        - Research Scientist
        - Biotech Entrepreneur
        """
        start_time = time.time()
        task_id, name = self.base_executor.get_task_id_and_name(task)

        logger.info(f"Starting multi-expert evaluation execution for task {task_id}: {name}")

        # Build task context for evaluation
        task_context = self.base_executor.build_task_context(task)
        task_context.update(
            {
                "evaluation_mode": "multi_expert",
                "quality_threshold": quality_threshold,
                "max_iterations": max_iterations,
                "experts": experts
                or [
                    "theoretical_biologist",
                    "clinical_physician",
                    "regulatory_expert",
                    "research_scientist",
                    "biotech_entrepreneur",
                ],
            }
        )

        # Prepare prompts
        default_prompt = self._build_domain_specific_prompt(name)
        base_prompt = self.base_executor.fetch_prompt(task_id, default_prompt)

        # Build contextual prompt if requested
        current_prompt = self.prompt_builder.build_context_prompt(
            task_id, name, base_prompt, use_context, context_options
        )

        # Execute iterative improvement with multi-expert evaluation
        result = self._execute_multi_expert_iterative_loop(
            task_id, name, current_prompt, task_context, max_iterations, quality_threshold
        )

        # Store final results
        self._finalize_task_execution(task_id, result)

        result.execution_time = time.time() - start_time

        # Monitor evaluation performance
        self._monitor_evaluation_performance(task_id, result)

        return result

    def _execute_multi_expert_iterative_loop(
        self,
        task_id: int,
        task_name: str,
        initial_prompt: str,
        task_context: Dict[str, Any],
        max_iterations: int,
        quality_threshold: float,
    ) -> TaskExecutionResult:
        """Execute the multi-expert evaluation iterative loop."""
        best_content = ""
        best_evaluation = None
        current_prompt = initial_prompt

        for iteration in range(max_iterations):
            try:
                logger.info(f"Multi-expert evaluation - Task {task_id} iteration {iteration + 1}/{max_iterations}")

                # Generate content
                content = self.base_executor.execute_llm_chat(current_prompt)

                # Multi-expert evaluation
                evaluation = self.expert_evaluator.evaluate_with_experts(
                    content=content, task_context=task_context, iteration=iteration
                )

                # Store evaluation history
                self._store_evaluation_history(task_id, iteration, content, evaluation)

                logger.info(
                    f"Task {task_id} iteration {iteration + 1} expert consensus score: {evaluation.overall_score:.3f}"
                )

                # Log expert details if available
                if evaluation.metadata and "expert_feedback" in evaluation.metadata:
                    expert_feedback = evaluation.metadata["expert_feedback"]
                    for expert, feedback in expert_feedback.items():
                        score = feedback.get("score", "N/A")
                        logger.debug(f"Expert {expert}: {score}")

                # Check quality threshold
                if evaluation.overall_score >= quality_threshold:
                    logger.info(
                        f"Task {task_id} reached multi-expert consensus threshold: {evaluation.overall_score:.3f}"
                    )
                    return TaskExecutionResult(
                        task_id=task_id, status="done", content=content, evaluation=evaluation, iterations=iteration + 1
                    )

                # Track best result
                if best_evaluation is None or evaluation.overall_score > best_evaluation.overall_score:
                    best_content = content
                    best_evaluation = evaluation

                # Build expert-informed revision prompt
                if iteration < max_iterations - 1:
                    current_prompt = self.prompt_builder.build_multi_expert_revision_prompt(
                        initial_prompt, content, evaluation, iteration
                    )

            except Exception as e:
                logger.error(f"Multi-expert evaluation iteration {iteration + 1} failed for task {task_id}: {e}")
                continue

        # Return best result
        final_status = "done" if best_content else "failed"
        if best_evaluation:
            logger.warning(
                f"Task {task_id} completed with best expert consensus score: {best_evaluation.overall_score:.3f}"
            )

        return TaskExecutionResult(
            task_id=task_id,
            status=final_status,
            content=best_content,
            evaluation=best_evaluation,
            iterations=max_iterations,
        )

    def _build_domain_specific_prompt(self, task_name: str) -> str:
        """Build prompt optimized for multi-expert evaluation in biomedical domain."""
        return f"""Please complete the following biomedical/scientific task with high professional standards:

Task: {task_name}

Your response will be evaluated by multiple domain experts including:
- Theoretical Biologist (fundamental biological principles)
- Clinical Physician (medical relevance and safety)
- Regulatory Expert (compliance and standards)
- Research Scientist (methodology and evidence)
- Biotech Entrepreneur (practical applications)

Requirements:
- Write approximately 200-300 words
- Use precise scientific terminology
- Ensure clinical accuracy and safety considerations
- Include relevant methodology or evidence
- Consider regulatory and ethical implications
- Maintain professional academic tone
- Structure content logically with clear sections

Please provide content that would satisfy all expert perspectives:"""

    def _finalize_task_execution(self, task_id: int, result: TaskExecutionResult):
        """Store final execution results."""
        try:
            if result.status == "done" and result.content:
                self.repo.upsert_task_output(task_id, result.content)
                self.repo.update_task_status(task_id, "done")
                # Generate embedding asynchronously
                self.base_executor.generate_task_embedding_async(task_id, result.content)
                logger.info(f"Task {task_id} completed successfully with multi-expert evaluation")
            else:
                self.repo.update_task_status(task_id, "failed")
                logger.warning(f"Task {task_id} failed to meet multi-expert evaluation standards")
        except Exception as e:
            logger.error(f"Failed to finalize task {task_id}: {e}")

    def _store_evaluation_history(self, task_id: int, iteration: int, content: str, evaluation):
        """Store multi-expert evaluation history."""
        try:
            # Extract expert details for metadata
            expert_details = {}
            if evaluation.metadata and "expert_feedback" in evaluation.metadata:
                expert_details = evaluation.metadata["expert_feedback"]

            metadata = {
                "evaluation_method": "multi_expert",
                "expert_count": len(expert_details),
                "consensus_info": evaluation.metadata.get("consensus_info", {}) if evaluation.metadata else {},
                "expert_feedback_summary": expert_details,
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
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to store multi-expert evaluation history: {e}")

    def _monitor_evaluation_performance(self, task_id: int, result: TaskExecutionResult):
        """Monitor multi-expert evaluation system performance."""
        try:
            monitor_evaluation(
                task_id=task_id,
                evaluation_method="multi_expert",
                final_score=result.evaluation.overall_score if result.evaluation else 0.0,
                iterations_used=result.iterations,
                execution_time=result.execution_time,
                success=(result.status == "done"),
            )
        except Exception as e:
            logger.warning(f"Failed to monitor evaluation performance: {e}")
