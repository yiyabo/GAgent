"""
CLI commands for evaluation system

Provides command-line interface for content evaluation and quality management.
"""

import os
import sys
from argparse import Namespace
from typing import Any, Dict, Optional

# Add app path for imports when running from tests
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from ..utils.io_utils import IOUtils
from .base import MultiCommand

# Import app modules safely
try:
    from ...app.execution.executors.enhanced import (
        execute_task_with_adversarial_evaluation,
        execute_task_with_evaluation,
        execute_task_with_llm_evaluation,
        execute_task_with_multi_expert_evaluation,
    )
    from ...app.models import EvaluationConfig
    from ...app.repository.tasks import default_repo
    from ...app.services.evaluation.evaluation_supervisor import (
        get_evaluation_supervisor,
        get_supervision_report,
    )
    from ...app.services.expert_evaluator import get_multi_expert_evaluator
except ImportError:
    # Fallback for when running from tests
    from app.execution.executors.enhanced import (
        execute_task_with_adversarial_evaluation,
        execute_task_with_evaluation,
        execute_task_with_llm_evaluation,
        execute_task_with_multi_expert_evaluation,
    )
    from app.models import EvaluationConfig
    from app.repository.tasks import default_repo
    from app.services.evaluation.evaluation_supervisor import (
        get_evaluation_supervisor,
        get_supervision_report,
    )
    from app.services.evaluation.expert_evaluator import get_multi_expert_evaluator


class EvaluationCommands(MultiCommand):
    """CLI commands for evaluation system"""

    @property
    def name(self) -> str:
        return "evaluation"

    @property
    def description(self) -> str:
        return "Content evaluation and quality management commands"

    def get_action_map(self) -> Dict[str, callable]:
        """Map evaluation arguments to handler methods."""
        return {
            "eval_config": self.handle_config,
            "eval_execute": self.handle_execute,
            "eval_llm": self.handle_llm_execute,
            "eval_multi_expert": self.handle_multi_expert_execute,
            "eval_adversarial": self.handle_adversarial_execute,
            "eval_history": self.handle_history,
            "eval_override": self.handle_override,
            "eval_stats": self.handle_stats,
            "eval_clear": self.handle_clear,
            "eval_batch": self.handle_batch,
            "eval_supervision": self.handle_supervision,
            "eval_supervision_config": self.handle_supervision_config,
        }

    def handle_default(self, args: Namespace) -> int:
        """Handle default evaluation behavior."""
        self.io.print_info("Available evaluation operations:")
        self.io.print_info("  --eval-config <task-id>       Configure evaluation settings")
        self.io.print_info("  --eval-execute <task-id>      Execute task with basic evaluation")
        self.io.print_info("  --eval-llm <task-id>          Execute task with LLM intelligent evaluation")
        self.io.print_info("  --eval-multi-expert <task-id> Execute task with multi-expert evaluation")
        self.io.print_info("  --eval-adversarial <task-id>  Execute task with adversarial evaluation")
        self.io.print_info("  --eval-history <task-id>      View evaluation history")
        self.io.print_info("  --eval-override <task-id>     Override evaluation result")
        self.io.print_info("  --eval-stats                  Show system statistics")
        self.io.print_info("  --eval-clear <task-id>        Clear evaluation history")
        self.io.print_info("  --eval-batch                  Run batch evaluation")
        self.io.print_info("  --eval-supervision            Show evaluation supervision report")
        self.io.print_info("  --eval-supervision-config     Configure supervision thresholds")
        return 0

    def handle_config(self, args: Namespace) -> int:
        """Handle evaluation configuration"""
        task_id = args.eval_config
        if not task_id:
            self.io.print_error("Task ID is required for configuration")
            return 1

        try:
            # Parse custom weights if provided
            custom_weights = None
            if hasattr(args, "weights") and args.weights:
                import json

                custom_weights = json.loads(args.weights)

            # Store configuration
            threshold = getattr(args, "threshold", 0.8)
            max_iterations = getattr(args, "max_iterations", 3)
            dimensions = getattr(args, "dimensions", None)
            domain_specific = getattr(args, "domain_specific", False)
            strict = getattr(args, "strict", False)

            default_repo.store_evaluation_config(
                task_id=task_id,
                quality_threshold=threshold,
                max_iterations=max_iterations,
                evaluation_dimensions=dimensions,
                domain_specific=domain_specific,
                strict_mode=strict,
                custom_weights=custom_weights,
            )

            self.io.print_success(f"Evaluation configuration updated for task {task_id}")
            self.io.print_info(f"  Quality threshold: {threshold}")
            self.io.print_info(f"  Max iterations: {max_iterations}")
            self.io.print_info(f"  Strict mode: {strict}")
            self.io.print_info(f"  Domain-specific: {domain_specific}")

            if dimensions:
                self.io.print_info(f"  Dimensions: {', '.join(dimensions)}")
            if custom_weights:
                self.io.print_info(f"  Custom weights: {custom_weights}")

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to configure evaluation: {e}")
            return 1

    def handle_execute(self, args: Namespace) -> int:
        """Handle task execution with evaluation"""
        task_id = args.eval_execute
        if not task_id:
            self.io.print_error("Task ID is required for execution")
            return 1

        try:
            # Get task info
            task = default_repo.get_task_info(task_id)
            if not task:
                self.io.print_error(f"Task {task_id} not found")
                return 1

            threshold = getattr(args, "threshold", 0.8)
            max_iterations = getattr(args, "max_iterations", 3)
            use_context = getattr(args, "use_context", False)
            verbose = getattr(args, "verbose", False)

            self.io.print_info(f"Executing task {task_id}: {task['name']}")
            self.io.print_info(f"Quality threshold: {threshold}")
            self.io.print_info(f"Max iterations: {max_iterations}")

            if verbose:
                self.io.print_info("Verbose mode enabled - showing detailed progress")

            # Execute with evaluation
            result = execute_task_with_evaluation(
                task=task,
                repo=default_repo,
                max_iterations=max_iterations,
                quality_threshold=threshold,
                use_context=use_context,
                context_options={"generate_embeddings": True} if use_context else None,
            )

            # Update task status
            default_repo.update_task_status(task_id, result.status)

            # Display results
            self._display_execution_result(result, verbose)

            return 0 if result.status in ["done", "needs_review"] else 1

        except Exception as e:
            self.io.print_error(f"Execution failed: {e}")
            return 1

    def handle_history(self, args: Namespace) -> int:
        """Handle evaluation history viewing"""
        task_id = args.eval_history
        if not task_id:
            self.io.print_error("Task ID is required for history")
            return 1

        try:
            latest = getattr(args, "latest", False)
            summary = getattr(args, "summary", False)

            if latest:
                # Show only latest evaluation
                evaluation = default_repo.get_latest_evaluation(task_id)
                if not evaluation:
                    self.io.print_warning(f"No evaluation history found for task {task_id}")
                    return 0

                self._display_single_evaluation(evaluation)
            else:
                # Show full history
                history = default_repo.get_evaluation_history(task_id)
                if not history:
                    self.io.print_warning(f"No evaluation history found for task {task_id}")
                    return 0

                self._display_evaluation_history(history, summary)

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to get evaluation history: {e}")
            return 1

    def handle_override(self, args: Namespace) -> int:
        """Handle evaluation override"""
        task_id = args.eval_override
        if not task_id:
            self.io.print_error("Task ID is required for override")
            return 1

        try:
            score = getattr(args, "score", None)
            if score is None:
                self.io.print_error("Score is required for override")
                return 1

            if not (0.0 <= score <= 1.0):
                self.io.print_error("Score must be between 0.0 and 1.0")
                return 1

            feedback = getattr(args, "feedback", None)
            reason = getattr(args, "reason", None)

            # Get latest evaluation
            latest_eval = default_repo.get_latest_evaluation(task_id)
            if not latest_eval:
                self.io.print_error(f"No evaluation found for task {task_id}")
                return 1

            # Store override
            iteration = latest_eval["iteration"] + 1
            metadata = {
                "override": True,
                "original_score": latest_eval["overall_score"],
                "human_feedback": feedback or "",
                "override_reason": reason or "",
                "override_timestamp": default_repo.get_latest_evaluation(task_id)["timestamp"],
            }

            default_repo.store_evaluation_history(
                task_id=task_id,
                iteration=iteration,
                content=latest_eval["content"],
                overall_score=score,
                dimension_scores=latest_eval["dimension_scores"],
                suggestions=[feedback] if feedback else [],
                needs_revision=score < 0.8,
                metadata=metadata,
            )

            self.io.print_success(f"Evaluation override applied for task {task_id}")
            self.io.print_info(f"  Previous score: {latest_eval['overall_score']:.3f}")
            self.io.print_info(f"  New score: {score:.3f}")
            if feedback:
                self.io.print_info(f"  Feedback: {feedback}")

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to override evaluation: {e}")
            return 1

    def handle_stats(self, args: Namespace) -> int:
        """Handle evaluation statistics"""
        try:
            detailed = getattr(args, "detailed", False)
            stats = default_repo.get_evaluation_stats()

            self.io.print_info("=== Evaluation System Statistics ===")
            self.io.print_info(f"Total evaluations: {stats['total_evaluations']}")
            self.io.print_info(f"Average score: {stats['average_score']:.3f}")
            self.io.print_info(f"Average iterations: {stats['average_iterations']:.1f}")
            self.io.print_info(f"Max iterations used: {stats['max_iterations_used']}")

            if detailed and stats["quality_distribution"]:
                self.io.print_info("\nQuality Distribution:")
                for tier, count in stats["quality_distribution"].items():
                    percentage = (count / stats["total_evaluations"] * 100) if stats["total_evaluations"] > 0 else 0
                    self.io.print_info(f"  {tier}: {count} ({percentage:.1f}%)")

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to get statistics: {e}")
            return 1

    def handle_clear(self, args: Namespace) -> int:
        """Handle evaluation history clearing"""
        task_id = args.eval_clear
        if not task_id:
            self.io.print_error("Task ID is required for clearing")
            return 1

        try:
            confirm = getattr(args, "confirm", False)
            if not confirm:
                if not self.confirm_action(f"Are you sure you want to clear evaluation history for task {task_id}?"):
                    self.io.print_info("Operation cancelled")
                    return 0

            default_repo.delete_evaluation_history(task_id)
            self.io.print_success(f"Evaluation history cleared for task {task_id}")

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to clear history: {e}")
            return 1

    def handle_batch(self, args: Namespace) -> int:
        """Handle batch evaluation"""
        try:
            task_ids = getattr(args, "task_ids", None)
            if not task_ids:
                self.io.print_error("No task IDs provided")
                return 1

            threshold = getattr(args, "threshold", 0.8)
            max_iterations = getattr(args, "max_iterations", 3)

            self.io.print_info(f"Starting batch evaluation of {len(task_ids)} tasks")

            results = []
            for task_id in task_ids:
                task = default_repo.get_task_info(task_id)
                if not task:
                    self.io.print_warning(f"Task {task_id} not found, skipping")
                    continue

                self.io.print_info(f"Evaluating task {task_id}: {task['name']}")

                result = execute_task_with_evaluation(
                    task=task, repo=default_repo, max_iterations=max_iterations, quality_threshold=threshold
                )

                default_repo.update_task_status(task_id, result.status)
                results.append(result)

                # Brief result display
                score = result.evaluation.overall_score if result.evaluation else 0.0
                self.io.print_info(f"  Result: {result.status} (score: {score:.3f}, iterations: {result.iterations})")

            # Summary
            successful = sum(1 for r in results if r.status == "done")
            needs_review = sum(1 for r in results if r.status == "needs_review")
            failed = sum(1 for r in results if r.status == "failed")

            self.io.print_success(f"\nBatch evaluation completed:")
            self.io.print_info(f"  Successful: {successful}")
            self.io.print_info(f"  Needs review: {needs_review}")
            self.io.print_info(f"  Failed: {failed}")

            return 0 if failed == 0 else 1

        except Exception as e:
            self.io.print_error(f"Batch evaluation failed: {e}")
            return 1

    def _handle_config_command(self, args) -> bool:
        """Handle evaluation configuration"""
        try:
            # Parse custom weights if provided
            custom_weights = None
            if args.weights:
                import json

                custom_weights = json.loads(args.weights)

            # Store configuration
            default_repo.store_evaluation_config(
                task_id=args.task_id,
                quality_threshold=args.threshold,
                max_iterations=args.max_iterations,
                evaluation_dimensions=args.dimensions,
                domain_specific=args.domain_specific,
                strict_mode=args.strict,
                custom_weights=custom_weights,
            )

            self.io.print_success(f"Evaluation configuration updated for task {args.task_id}")
            self.io.print_info(f"  Quality threshold: {args.threshold}")
            self.io.print_info(f"  Max iterations: {args.max_iterations}")
            self.io.print_info(f"  Strict mode: {args.strict}")
            self.io.print_info(f"  Domain-specific: {args.domain_specific}")

            if args.dimensions:
                self.io.print_info(f"  Dimensions: {', '.join(args.dimensions)}")
            if custom_weights:
                self.io.print_info(f"  Custom weights: {custom_weights}")

            return True

        except Exception as e:
            self.io.print_error(f"Failed to configure evaluation: {e}")
            return False

    def _handle_execute_command(self, args) -> bool:
        """Handle task execution with evaluation"""
        try:
            # Get task info
            task = default_repo.get_task_info(args.task_id)
            if not task:
                self.io.print_error(f"Task {args.task_id} not found")
                return False

            self.io.print_info(f"Executing task {args.task_id}: {task['name']}")
            self.io.print_info(f"Quality threshold: {args.threshold}")
            self.io.print_info(f"Max iterations: {args.max_iterations}")

            if args.verbose:
                self.io.print_info("Verbose mode enabled - showing detailed progress")

            # Execute with evaluation
            result = execute_task_with_evaluation(
                task=task,
                repo=default_repo,
                max_iterations=args.max_iterations,
                quality_threshold=args.threshold,
                use_context=args.use_context,
                context_options={"generate_embeddings": True} if args.use_context else None,
            )

            # Update task status
            default_repo.update_task_status(args.task_id, result.status)

            # Display results
            self._display_execution_result(result, args.verbose)

            return result.status in ["done", "needs_review"]

        except Exception as e:
            self.io.print_error(f"Execution failed: {e}")
            return False

    def _handle_history_command(self, args) -> bool:
        """Handle evaluation history viewing"""
        try:
            if args.latest:
                # Show only latest evaluation
                evaluation = default_repo.get_latest_evaluation(args.task_id)
                if not evaluation:
                    self.io.print_warning(f"No evaluation history found for task {args.task_id}")
                    return True

                self._display_single_evaluation(evaluation)
            else:
                # Show full history
                history = default_repo.get_evaluation_history(args.task_id)
                if not history:
                    self.io.print_warning(f"No evaluation history found for task {args.task_id}")
                    return True

                self._display_evaluation_history(history, args.summary)

            return True

        except Exception as e:
            self.io.print_error(f"Failed to get evaluation history: {e}")
            return False

    def _handle_override_command(self, args) -> bool:
        """Handle evaluation override"""
        try:
            if not (0.0 <= args.score <= 1.0):
                self.io.print_error("Score must be between 0.0 and 1.0")
                return False

            # Get latest evaluation
            latest_eval = default_repo.get_latest_evaluation(args.task_id)
            if not latest_eval:
                self.io.print_error(f"No evaluation found for task {args.task_id}")
                return False

            # Store override
            iteration = latest_eval["iteration"] + 1
            metadata = {
                "override": True,
                "original_score": latest_eval["overall_score"],
                "human_feedback": args.feedback or "",
                "override_reason": args.reason or "",
                "override_timestamp": default_repo.get_latest_evaluation(args.task_id)["timestamp"],
            }

            default_repo.store_evaluation_history(
                task_id=args.task_id,
                iteration=iteration,
                content=latest_eval["content"],
                overall_score=args.score,
                dimension_scores=latest_eval["dimension_scores"],
                suggestions=[args.feedback] if args.feedback else [],
                needs_revision=args.score < 0.8,
                metadata=metadata,
            )

            self.io.print_success(f"Evaluation override applied for task {args.task_id}")
            self.io.print_info(f"  Previous score: {latest_eval['overall_score']:.3f}")
            self.io.print_info(f"  New score: {args.score:.3f}")
            if args.feedback:
                self.io.print_info(f"  Feedback: {args.feedback}")

            return True

        except Exception as e:
            self.io.print_error(f"Failed to override evaluation: {e}")
            return False

    def _handle_stats_command(self, args) -> bool:
        """Handle evaluation statistics"""
        try:
            stats = default_repo.get_evaluation_stats()

            self.io.print_info("=== Evaluation System Statistics ===")
            self.io.print_info(f"Total evaluations: {stats['total_evaluations']}")
            self.io.print_info(f"Average score: {stats['average_score']:.3f}")
            self.io.print_info(f"Average iterations: {stats['average_iterations']:.1f}")
            self.io.print_info(f"Max iterations used: {stats['max_iterations_used']}")

            if args.detailed and stats["quality_distribution"]:
                self.io.print_info("\nQuality Distribution:")
                for tier, count in stats["quality_distribution"].items():
                    percentage = (count / stats["total_evaluations"] * 100) if stats["total_evaluations"] > 0 else 0
                    self.io.print_info(f"  {tier}: {count} ({percentage:.1f}%)")

            return True

        except Exception as e:
            self.io.print_error(f"Failed to get statistics: {e}")
            return False

    def _handle_clear_command(self, args) -> bool:
        """Handle evaluation history clearing"""
        try:
            if not args.confirm:
                response = input(f"Are you sure you want to clear evaluation history for task {args.task_id}? (y/N): ")
                if response.lower() not in ["y", "yes"]:
                    self.io.print_info("Operation cancelled")
                    return True

            default_repo.delete_evaluation_history(args.task_id)
            self.io.print_success(f"Evaluation history cleared for task {args.task_id}")

            return True

        except Exception as e:
            self.io.print_error(f"Failed to clear history: {e}")
            return False

    def _handle_batch_command(self, args) -> bool:
        """Handle batch evaluation"""
        try:
            if not args.task_ids:
                self.io.print_error("No task IDs provided")
                return False

            self.io.print_info(f"Starting batch evaluation of {len(args.task_ids)} tasks")

            results = []
            for task_id in args.task_ids:
                task = default_repo.get_task_info(task_id)
                if not task:
                    self.io.print_warning(f"Task {task_id} not found, skipping")
                    continue

                self.io.print_info(f"Evaluating task {task_id}: {task['name']}")

                result = execute_task_with_evaluation(
                    task=task, repo=default_repo, max_iterations=args.max_iterations, quality_threshold=args.threshold
                )

                default_repo.update_task_status(task_id, result.status)
                results.append(result)

                # Brief result display
                score = result.evaluation.overall_score if result.evaluation else 0.0
                self.io.print_info(f"  Result: {result.status} (score: {score:.3f}, iterations: {result.iterations})")

            # Summary
            successful = sum(1 for r in results if r.status == "done")
            needs_review = sum(1 for r in results if r.status == "needs_review")
            failed = sum(1 for r in results if r.status == "failed")

            self.io.print_success(f"\nBatch evaluation completed:")
            self.io.print_info(f"  Successful: {successful}")
            self.io.print_info(f"  Needs review: {needs_review}")
            self.io.print_info(f"  Failed: {failed}")

            return failed == 0

        except Exception as e:
            self.io.print_error(f"Batch evaluation failed: {e}")
            return False

    def _display_execution_result(self, result, verbose: bool = False):
        """Display task execution result"""
        if result.evaluation:
            score = result.evaluation.overall_score
            status_color = (
                "success" if result.status == "done" else "warning" if result.status == "needs_review" else "error"
            )

            self.io.print_info(f"\n=== Execution Result ===")
            getattr(self.io, f"print_{status_color}")(f"Status: {result.status.upper()}")
            self.io.print_info(f"Final score: {score:.3f}")
            self.io.print_info(f"Iterations: {result.iterations}")
            self.io.print_info(f"Execution time: {result.execution_time:.2f}s")

            if verbose and result.evaluation.dimensions:
                self.io.print_info("\nDimension Scores:")
                for dim, score in result.evaluation.dimensions.dict().items():
                    if score > 0:  # Only show evaluated dimensions
                        self.io.print_info(f"  {dim}: {score:.3f}")

            if result.evaluation.suggestions:
                self.io.print_info("\nSuggestions:")
                for suggestion in result.evaluation.suggestions:
                    self.io.print_info(f"  • {suggestion}")
        else:
            self.io.print_error("No evaluation result available")

    def _display_single_evaluation(self, evaluation):
        """Display a single evaluation"""
        self.io.print_info(f"=== Evaluation (Iteration {evaluation['iteration']}) ===")
        self.io.print_info(f"Overall score: {evaluation['overall_score']:.3f}")
        self.io.print_info(f"Needs revision: {evaluation['needs_revision']}")
        self.io.print_info(f"Timestamp: {evaluation['timestamp']}")

        if evaluation["dimension_scores"]:
            self.io.print_info("\nDimension Scores:")
            for dim, score in evaluation["dimension_scores"].items():
                self.io.print_info(f"  {dim}: {score:.3f}")

        if evaluation["suggestions"]:
            self.io.print_info("\nSuggestions:")
            for suggestion in evaluation["suggestions"]:
                self.io.print_info(f"  • {suggestion}")

    def _display_evaluation_history(self, history, summary_only: bool = False):
        """Display evaluation history"""
        self.io.print_info(f"=== Evaluation History ({len(history)} iterations) ===")

        if summary_only:
            scores = [h["overall_score"] for h in history]
            self.io.print_info(f"Score progression: {' → '.join(f'{s:.3f}' for s in scores)}")
            self.io.print_info(f"Best score: {max(scores):.3f}")
            self.io.print_info(f"Latest score: {scores[-1]:.3f}")
        else:
            for i, eval_data in enumerate(history):
                self.io.print_info(f"\nIteration {eval_data['iteration']}:")
                self.io.print_info(f"  Score: {eval_data['overall_score']:.3f}")
                self.io.print_info(f"  Needs revision: {eval_data['needs_revision']}")
                if eval_data["suggestions"]:
                    self.io.print_info(f"  Suggestions: {len(eval_data['suggestions'])} items")

    def handle_llm_execute(self, args: Namespace) -> int:
        """Execute task with LLM-based intelligent evaluation"""
        try:
            task_id = args.eval_llm
            quality_threshold = getattr(args, "threshold", 0.8)
            max_iterations = getattr(args, "max_iterations", 3)
            use_context = getattr(args, "use_context", False)

            self.io.print_info(f"🧠 Executing task {task_id} with LLM intelligent evaluation")
            self.io.print_info(f"   Quality threshold: {quality_threshold}")
            self.io.print_info(f"   Max iterations: {max_iterations}")

            # Get task
            task = default_repo.get_task_info(task_id)
            if not task:
                self.io.print_error(f"Task {task_id} not found")
                return 1

            # Execute with LLM evaluation
            result = execute_task_with_llm_evaluation(
                task=task,
                repo=default_repo,
                max_iterations=max_iterations,
                quality_threshold=quality_threshold,
                use_context=use_context,
            )

            self.io.print_success(f"✅ LLM evaluation completed!")
            self.io.print_info(f"   Final status: {result.status}")
            self.io.print_info(f"   Final score: {result.evaluation.overall_score:.3f}")
            self.io.print_info(f"   Iterations: {result.iterations_completed}")
            self.io.print_info(f"   Execution time: {result.execution_time:.2f}s")

            # Show evaluation details
            eval_result = result.evaluation
            self.io.print_info(f"   📊 Dimension scores:")
            self.io.print_info(f"      相关性: {eval_result.dimensions.relevance:.3f}")
            self.io.print_info(f"      完整性: {eval_result.dimensions.completeness:.3f}")
            self.io.print_info(f"      准确性: {eval_result.dimensions.accuracy:.3f}")
            self.io.print_info(f"      清晰度: {eval_result.dimensions.clarity:.3f}")
            self.io.print_info(f"      连贯性: {eval_result.dimensions.coherence:.3f}")
            self.io.print_info(f"      科学严谨性: {eval_result.dimensions.scientific_rigor:.3f}")

            if eval_result.suggestions:
                self.io.print_info(f"   💡 Improvement suggestions:")
                for i, suggestion in enumerate(eval_result.suggestions[:3], 1):
                    self.io.print_info(f"      {i}. {suggestion}")

            return 0

        except Exception as e:
            self.io.print_error(f"LLM evaluation execution failed: {e}")
            return 1

    def handle_multi_expert_execute(self, args: Namespace) -> int:
        """Execute task with multi-expert evaluation system"""
        try:
            task_id = args.eval_multi_expert
            if not task_id:
                self.io.print_error("Task ID is required for multi-expert evaluation")
                return 1

            quality_threshold = getattr(args, "threshold", 0.8)
            max_iterations = getattr(args, "max_iterations", 3)
            use_context = getattr(args, "use_context", False)
            experts = getattr(args, "experts", "").split(",") if hasattr(args, "experts") and args.experts else None

            if experts:
                experts = [e.strip() for e in experts if e.strip()]
                self.io.print_info(f"🎭 Executing task {task_id} with selected experts: {', '.join(experts)}")
            else:
                self.io.print_info(f"🎭 Executing task {task_id} with all available experts")

            self.io.print_info(f"   Quality threshold: {quality_threshold}")
            self.io.print_info(f"   Max iterations: {max_iterations}")

            # Get task
            task = default_repo.get_task_info(task_id)
            if not task:
                self.io.print_error(f"Task {task_id} not found")
                return 1

            # Execute with multi-expert evaluation
            result = execute_task_with_multi_expert_evaluation(
                task=task,
                repo=default_repo,
                max_iterations=max_iterations,
                quality_threshold=quality_threshold,
                selected_experts=experts,
                use_context=use_context,
            )

            self.io.print_success(f"✅ Multi-expert evaluation completed!")
            self.io.print_info(f"   Final status: {result.status}")
            self.io.print_info(f"   Consensus score: {result.evaluation.overall_score:.3f}")
            self.io.print_info(f"   Iterations: {result.iterations_completed}")
            self.io.print_info(f"   Execution time: {result.execution_time:.2f}s")

            # Show expert details from metadata
            metadata = result.metadata or {}
            expert_evaluations = metadata.get("expert_evaluations", {})
            disagreements = metadata.get("disagreements", [])
            consensus_confidence = metadata.get("consensus_confidence", 0.0)

            if expert_evaluations:
                self.io.print_info(f"\n   👥 Expert Scores:")
                for expert_name, evaluation in expert_evaluations.items():
                    expert_role = evaluation.get("expert_role", expert_name)
                    overall_score = evaluation.get("overall_score", 0)
                    confidence = evaluation.get("confidence_level", 0)
                    self.io.print_info(f"      {expert_role}: {overall_score:.3f} (信心度: {confidence:.2f})")

                self.io.print_info(f"   🤝 Consensus confidence: {consensus_confidence:.3f}")

            if disagreements:
                self.io.print_info(f"\n   🔥 Expert disagreements: {len(disagreements)} areas")
                for disagreement in disagreements[:3]:  # Show top 3
                    field = disagreement["field"]
                    level = disagreement["disagreement_level"]
                    self.io.print_info(f"      {field}: disagreement level {level:.2f}")

            return 0 if result.status in ["done", "needs_review"] else 1

        except Exception as e:
            self.io.print_error(f"Multi-expert evaluation execution failed: {e}")
            return 1

    def handle_adversarial_execute(self, args: Namespace) -> int:
        """Execute task with adversarial evaluation (Generator vs Critic)"""
        try:
            task_id = args.eval_adversarial
            if not task_id:
                self.io.print_error("Task ID is required for adversarial evaluation")
                return 1

            max_rounds = getattr(args, "max_rounds", 3)
            improvement_threshold = getattr(args, "improvement_threshold", 0.1)
            use_context = getattr(args, "use_context", False)

            self.io.print_info(f"⚔️  Executing task {task_id} with adversarial evaluation")
            self.io.print_info(f"   Max rounds: {max_rounds}")
            self.io.print_info(f"   Improvement threshold: {improvement_threshold}")

            # Get task
            task = default_repo.get_task_info(task_id)
            if not task:
                self.io.print_error(f"Task {task_id} not found")
                return 1

            # Execute with adversarial evaluation
            result = execute_task_with_adversarial_evaluation(
                task=task,
                repo=default_repo,
                max_rounds=max_rounds,
                improvement_threshold=improvement_threshold,
                use_context=use_context,
            )

            self.io.print_success(f"✅ Adversarial evaluation completed!")
            self.io.print_info(f"   Final status: {result.status}")
            self.io.print_info(f"   Robustness score: {result.evaluation.overall_score:.3f}")
            self.io.print_info(f"   Rounds completed: {result.iterations_completed}")
            self.io.print_info(f"   Execution time: {result.execution_time:.2f}s")

            # Show adversarial details from metadata
            metadata = result.metadata or {}
            adversarial_effectiveness = metadata.get("adversarial_effectiveness", 0.0)
            robustness_score = metadata.get("robustness_score", 0.0)

            self.io.print_info(f"\n   ⚔️  Adversarial Analysis:")
            self.io.print_info(f"      Adversarial effectiveness: {adversarial_effectiveness:.3f}")
            self.io.print_info(f"      Final robustness: {robustness_score:.3f}")

            # Show evaluation suggestions
            if result.evaluation and result.evaluation.suggestions:
                self.io.print_info(f"\n   💡 Adversarial Insights:")
                for i, suggestion in enumerate(result.evaluation.suggestions[:3], 1):
                    self.io.print_info(f"      {i}. {suggestion}")

            return 0 if result.status in ["done", "needs_review"] else 1

        except Exception as e:
            self.io.print_error(f"Adversarial evaluation execution failed: {e}")
            return 1

    def handle_multi_expert_analysis(self, args: Namespace) -> int:
        """Evaluate content with multiple expert perspectives"""
        try:
            task_id = args.eval_multi_expert
            experts = getattr(args, "experts", "").split(",") if hasattr(args, "experts") and args.experts else None

            if experts:
                experts = [e.strip() for e in experts if e.strip()]
                self.io.print_info(f"🎭 Selected experts: {', '.join(experts)}")
            else:
                self.io.print_info(f"🎭 Using all available experts")

            # Get task content
            task = default_repo.get_task_info(task_id)
            if not task:
                self.io.print_error(f"Task {task_id} not found")
                return 1

            # Get task content from task_outputs
            task_content = default_repo.get_task_output_content(task_id)
            if not task_content:
                self.io.print_error(f"Task {task_id} has no content to evaluate")
                return 1

            # Multi-expert evaluation
            evaluator = get_multi_expert_evaluator()
            task_context = {"name": task.get("name", f"Task {task_id}"), "task_type": "content_evaluation"}

            result = evaluator.evaluate_with_multiple_experts(
                content=task_content, task_context=task_context, selected_experts=experts, iteration=1
            )

            # Display results
            expert_evaluations = result.get("expert_evaluations", {})
            consensus = result.get("consensus", {})
            disagreements = result.get("disagreements", [])

            self.io.print_success(f"✅ Multi-expert evaluation completed!")
            self.io.print_info(f"   Participating experts: {len(expert_evaluations)}")

            # Individual expert scores
            self.io.print_info(f"\n   👥 Individual Expert Scores:")
            for expert_name, evaluation in expert_evaluations.items():
                expert_role = evaluation.get("expert_role", expert_name)
                overall_score = evaluation.get("overall_score", 0)
                confidence = evaluation.get("confidence_level", 0)
                self.io.print_info(f"      {expert_role}: {overall_score:.3f} (信心度: {confidence:.2f})")

            # Consensus results
            self.io.print_info(f"\n   🤝 Expert Consensus:")
            self.io.print_info(f"      Overall Score: {consensus.get('overall_score', 0):.3f}")
            self.io.print_info(f"      Consensus Confidence: {consensus.get('consensus_confidence', 0):.3f}")

            # Show disagreements
            if disagreements:
                self.io.print_info(f"\n   🔥 Expert Disagreements:")
                for disagreement in disagreements:
                    field = disagreement["field"]
                    level = disagreement["disagreement_level"]
                    lowest = disagreement["lowest_scorer"]
                    highest = disagreement["highest_scorer"]
                    self.io.print_info(f"      {field}: {lowest} vs {highest} (分歧度: {level:.2f})")
            else:
                self.io.print_info(f"\n   ✅ No significant disagreements among experts")

            # Aggregate suggestions
            all_suggestions = consensus.get("specific_suggestions", [])
            if all_suggestions:
                self.io.print_info(f"\n   💡 Expert Recommendations:")
                for i, suggestion in enumerate(all_suggestions[:5], 1):
                    self.io.print_info(f"      {i}. {suggestion}")

            return 0

        except Exception as e:
            self.io.print_error(f"Multi-expert evaluation failed: {e}")
            return 1

    def handle_supervision(self, args: Namespace) -> int:
        """Handle evaluation supervision report"""
        try:
            detailed = getattr(args, "detailed", False)

            self.io.print_info("🔍 Generating evaluation supervision report...")

            # Get supervision report
            supervision_report = get_supervision_report()

            # Display system health
            system_health = supervision_report.get("system_health", {})
            overall_score = system_health.get("overall_score", 0.0)
            status = system_health.get("status", "unknown")

            status_color = "success" if status == "healthy" else "warning" if status == "degraded" else "error"

            self.io.print_info("\n=== 评估系统监督报告 ===")
            getattr(self.io, f"print_{status_color}")(f"系统健康状态: {status.upper()}")
            self.io.print_info(f"整体健康评分: {overall_score:.3f}")
            self.io.print_info(f"报告时间: {supervision_report.get('timestamp', 'N/A')}")

            # Current metrics
            current_metrics = supervision_report.get("current_metrics", {})
            if current_metrics:
                self.io.print_info("\n📊 当前质量指标:")
                for metric_name, metric_data in current_metrics.items():
                    value = metric_data.get("value", 0.0)
                    status = metric_data.get("status", "unknown")
                    threshold = metric_data.get("threshold", 0.0)

                    metric_color = "success" if status == "good" else "warning" if status == "warning" else "error"
                    self.io.print_info(f"  {metric_name}: {value:.3f} (阈值: {threshold:.3f})", end="")
                    getattr(self.io, f"print_{metric_color}")(f" [{status}]")

            # Performance summary
            performance_summary = supervision_report.get("performance_summary", {})
            if performance_summary:
                self.io.print_info("\n⚡ 性能摘要:")
                avg_time = performance_summary.get("avg_evaluation_time", 0.0)
                max_time = performance_summary.get("max_evaluation_time", 0.0)
                success_rate = performance_summary.get("success_rate", 0.0)
                cache_hit_rate = performance_summary.get("avg_cache_hit_rate", 0.0)

                self.io.print_info(f"  平均评估时间: {avg_time:.2f}s")
                self.io.print_info(f"  最大评估时间: {max_time:.2f}s")
                self.io.print_info(f"  成功率: {success_rate:.1%}")
                self.io.print_info(f"  缓存命中率: {cache_hit_rate:.1%}")

            # Quality trends
            if detailed:
                quality_trends = supervision_report.get("quality_trends", {})
                if quality_trends:
                    self.io.print_info("\n📈 质量趋势:")
                    for metric_name, trend_data in quality_trends.items():
                        trend = trend_data.get("trend", "stable")
                        recent_avg = trend_data.get("recent_avg", 0.0)
                        historical_avg = trend_data.get("historical_avg", 0.0)

                        trend_symbol = "📈" if trend == "improving" else "📉" if trend == "declining" else "➡️"
                        self.io.print_info(
                            f"  {metric_name}: {trend_symbol} {trend} (当前: {recent_avg:.3f}, 历史: {historical_avg:.3f})"
                        )

            # Recent alerts
            alert_summary = supervision_report.get("alert_summary", {})
            total_alerts = alert_summary.get("total", 0)
            critical_alerts = alert_summary.get("critical", 0)
            high_alerts = alert_summary.get("high", 0)

            if total_alerts > 0:
                self.io.print_warning(f"\n🚨 最近24小时警报: {total_alerts} 个")
                if critical_alerts > 0:
                    self.io.print_error(f"  严重警报: {critical_alerts}")
                if high_alerts > 0:
                    self.io.print_warning(f"  高级警报: {high_alerts}")

                # Show recent alerts if detailed
                if detailed:
                    recent_alerts = supervision_report.get("recent_alerts", [])
                    if recent_alerts:
                        self.io.print_info("\n最近警报详情:")
                        for alert in recent_alerts[:5]:  # Show top 5
                            alert_type = alert.get("alert_type", "unknown")
                            severity = alert.get("severity", "unknown")
                            message = alert.get("message", "")
                            timestamp = alert.get("timestamp", "")

                            severity_color = (
                                "error"
                                if severity == "critical"
                                else "warning" if severity in ["high", "medium"] else "info"
                            )
                            getattr(self.io, f"print_{severity_color}")(
                                f"  [{severity.upper()}] {alert_type}: {message}"
                            )
                            self.io.print_info(f"    时间: {timestamp}")
            else:
                self.io.print_success("\n✅ 最近24小时无警报")

            # Calibration history
            calibration_history = supervision_report.get("calibration_history", [])
            if calibration_history and detailed:
                self.io.print_info(f"\n🔧 最近自动校准: {len(calibration_history)} 次")
                for calibration in calibration_history[-3:]:  # Show last 3
                    action = calibration.get("action", "unknown")
                    metric = calibration.get("metric", "unknown")
                    reason = calibration.get("reason", "unknown")
                    timestamp = calibration.get("timestamp", "")
                    self.io.print_info(f"  {action} ({metric}): {reason} - {timestamp}")

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to get supervision report: {e}")
            return 1

    def handle_supervision_config(self, args: Namespace) -> int:
        """Handle supervision configuration"""
        try:
            # Parse threshold updates
            thresholds = {}

            # Check for common threshold arguments
            if hasattr(args, "min_accuracy") and args.min_accuracy is not None:
                thresholds["min_accuracy"] = float(args.min_accuracy)
            if hasattr(args, "min_consistency") and args.min_consistency is not None:
                thresholds["min_consistency"] = float(args.min_consistency)
            if hasattr(args, "max_bias_risk") and args.max_bias_risk is not None:
                thresholds["max_bias_risk"] = float(args.max_bias_risk)
            if hasattr(args, "min_cache_hit_rate") and args.min_cache_hit_rate is not None:
                thresholds["min_cache_hit_rate"] = float(args.min_cache_hit_rate)
            if hasattr(args, "max_error_rate") and args.max_error_rate is not None:
                thresholds["max_error_rate"] = float(args.max_error_rate)
            if hasattr(args, "max_evaluation_time") and args.max_evaluation_time is not None:
                thresholds["max_evaluation_time"] = float(args.max_evaluation_time)
            if hasattr(args, "min_confidence") and args.min_confidence is not None:
                thresholds["min_confidence"] = float(args.min_confidence)

            if not thresholds:
                # Show current configuration
                supervision_report = get_supervision_report()
                supervision_config = supervision_report.get("supervision_config", {})
                current_thresholds = supervision_config.get("thresholds", {})
                auto_calibration = supervision_config.get("auto_calibration_enabled", False)

                self.io.print_info("=== 当前监督系统配置 ===")
                self.io.print_info(f"自动校准: {'启用' if auto_calibration else '禁用'}")
                self.io.print_info("\n监督阈值:")
                for threshold_name, value in current_thresholds.items():
                    self.io.print_info(f"  {threshold_name}: {value}")

                self.io.print_info("\n可配置的阈值:")
                self.io.print_info("  --min-accuracy <value>        最小准确率阈值 (0.0-1.0)")
                self.io.print_info("  --min-consistency <value>     最小一致性阈值 (0.0-1.0)")
                self.io.print_info("  --max-bias-risk <value>       最大偏见风险阈值 (0.0-1.0)")
                self.io.print_info("  --min-cache-hit-rate <value>  最小缓存命中率阈值 (0.0-1.0)")
                self.io.print_info("  --max-error-rate <value>      最大错误率阈值 (0.0-1.0)")
                self.io.print_info("  --max-evaluation-time <value> 最大评估时间阈值 (秒)")
                self.io.print_info("  --min-confidence <value>      最小置信度阈值 (0.0-1.0)")

                return 0

            # Update thresholds
            supervisor = get_evaluation_supervisor()
            success = supervisor.update_thresholds(thresholds)

            if success:
                self.io.print_success("监督系统阈值更新成功!")
                self.io.print_info("更新的阈值:")
                for threshold_name, value in thresholds.items():
                    self.io.print_info(f"  {threshold_name}: {value}")
            else:
                self.io.print_error("监督系统阈值更新失败")
                return 1

            return 0

        except Exception as e:
            self.io.print_error(f"Failed to configure supervision: {e}")
            return 1


def register_evaluation_commands():
    """Register evaluation commands with CLI"""
    return EvaluationCommands()
