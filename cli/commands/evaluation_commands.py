"""
CLI commands for evaluation system

Provides command-line interface for content evaluation and quality management.
"""

from typing import Optional, Dict, Any
from argparse import Namespace
import argparse
import sys
import os

# Add app path for imports when running from tests
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from .base import MultiCommand
from ..utils.io_utils import IOUtils

# Import app modules safely
try:
    from ...app.repository.tasks import default_repo
    from ...app.executor_enhanced import execute_task_with_evaluation
    from ...app.models import EvaluationConfig
except ImportError:
    # Fallback for when running from tests
    from app.repository.tasks import default_repo
    from app.executor_enhanced import execute_task_with_evaluation
    from app.models import EvaluationConfig


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
            'eval_config': self.handle_config,
            'eval_execute': self.handle_execute,
            'eval_history': self.handle_history,
            'eval_override': self.handle_override,
            'eval_stats': self.handle_stats,
            'eval_clear': self.handle_clear,
            'eval_batch': self.handle_batch,
        }
    
    def handle_default(self, args: Namespace) -> int:
        """Handle default evaluation behavior."""
        self.io.print_info("Available evaluation operations:")
        self.io.print_info("  --eval-config <task-id>    Configure evaluation settings")
        self.io.print_info("  --eval-execute <task-id>   Execute task with evaluation")
        self.io.print_info("  --eval-history <task-id>   View evaluation history")
        self.io.print_info("  --eval-override <task-id>  Override evaluation result")
        self.io.print_info("  --eval-stats               Show system statistics")
        self.io.print_info("  --eval-clear <task-id>     Clear evaluation history")
        self.io.print_info("  --eval-batch               Run batch evaluation")
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
            if hasattr(args, 'weights') and args.weights:
                import json
                custom_weights = json.loads(args.weights)
            
            # Store configuration
            threshold = getattr(args, 'threshold', 0.8)
            max_iterations = getattr(args, 'max_iterations', 3)
            dimensions = getattr(args, 'dimensions', None)
            domain_specific = getattr(args, 'domain_specific', False)
            strict = getattr(args, 'strict', False)
            
            default_repo.store_evaluation_config(
                task_id=task_id,
                quality_threshold=threshold,
                max_iterations=max_iterations,
                evaluation_dimensions=dimensions,
                domain_specific=domain_specific,
                strict_mode=strict,
                custom_weights=custom_weights
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
            
            threshold = getattr(args, 'threshold', 0.8)
            max_iterations = getattr(args, 'max_iterations', 3)
            use_context = getattr(args, 'use_context', False)
            verbose = getattr(args, 'verbose', False)
            
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
                context_options={"generate_embeddings": True} if use_context else None
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
            latest = getattr(args, 'latest', False)
            summary = getattr(args, 'summary', False)
            
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
            score = getattr(args, 'score', None)
            if score is None:
                self.io.print_error("Score is required for override")
                return 1
            
            if not (0.0 <= score <= 1.0):
                self.io.print_error("Score must be between 0.0 and 1.0")
                return 1
            
            feedback = getattr(args, 'feedback', None)
            reason = getattr(args, 'reason', None)
            
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
                "override_timestamp": default_repo.get_latest_evaluation(task_id)["timestamp"]
            }
            
            default_repo.store_evaluation_history(
                task_id=task_id,
                iteration=iteration,
                content=latest_eval["content"],
                overall_score=score,
                dimension_scores=latest_eval["dimension_scores"],
                suggestions=[feedback] if feedback else [],
                needs_revision=score < 0.8,
                metadata=metadata
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
            detailed = getattr(args, 'detailed', False)
            stats = default_repo.get_evaluation_stats()
            
            self.io.print_info("=== Evaluation System Statistics ===")
            self.io.print_info(f"Total evaluations: {stats['total_evaluations']}")
            self.io.print_info(f"Average score: {stats['average_score']:.3f}")
            self.io.print_info(f"Average iterations: {stats['average_iterations']:.1f}")
            self.io.print_info(f"Max iterations used: {stats['max_iterations_used']}")
            
            if detailed and stats['quality_distribution']:
                self.io.print_info("\nQuality Distribution:")
                for tier, count in stats['quality_distribution'].items():
                    percentage = (count / stats['total_evaluations'] * 100) if stats['total_evaluations'] > 0 else 0
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
            confirm = getattr(args, 'confirm', False)
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
            task_ids = getattr(args, 'task_ids', None)
            if not task_ids:
                self.io.print_error("No task IDs provided")
                return 1
            
            threshold = getattr(args, 'threshold', 0.8)
            max_iterations = getattr(args, 'max_iterations', 3)
            
            self.io.print_info(f"Starting batch evaluation of {len(task_ids)} tasks")
            
            results = []
            for task_id in task_ids:
                task = default_repo.get_task_info(task_id)
                if not task:
                    self.io.print_warning(f"Task {task_id} not found, skipping")
                    continue
                
                self.io.print_info(f"Evaluating task {task_id}: {task['name']}")
                
                result = execute_task_with_evaluation(
                    task=task,
                    repo=default_repo,
                    max_iterations=max_iterations,
                    quality_threshold=threshold
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
                custom_weights=custom_weights
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
                context_options={"generate_embeddings": True} if args.use_context else None
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
                "override_timestamp": default_repo.get_latest_evaluation(args.task_id)["timestamp"]
            }
            
            default_repo.store_evaluation_history(
                task_id=args.task_id,
                iteration=iteration,
                content=latest_eval["content"],
                overall_score=args.score,
                dimension_scores=latest_eval["dimension_scores"],
                suggestions=[args.feedback] if args.feedback else [],
                needs_revision=args.score < 0.8,
                metadata=metadata
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
            
            if args.detailed and stats['quality_distribution']:
                self.io.print_info("\nQuality Distribution:")
                for tier, count in stats['quality_distribution'].items():
                    percentage = (count / stats['total_evaluations'] * 100) if stats['total_evaluations'] > 0 else 0
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
                if response.lower() not in ['y', 'yes']:
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
                    task=task,
                    repo=default_repo,
                    max_iterations=args.max_iterations,
                    quality_threshold=args.threshold
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
            status_color = "success" if result.status == "done" else "warning" if result.status == "needs_review" else "error"
            
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
        
        if evaluation['dimension_scores']:
            self.io.print_info("\nDimension Scores:")
            for dim, score in evaluation['dimension_scores'].items():
                self.io.print_info(f"  {dim}: {score:.3f}")
        
        if evaluation['suggestions']:
            self.io.print_info("\nSuggestions:")
            for suggestion in evaluation['suggestions']:
                self.io.print_info(f"  • {suggestion}")
    
    def _display_evaluation_history(self, history, summary_only: bool = False):
        """Display evaluation history"""
        self.io.print_info(f"=== Evaluation History ({len(history)} iterations) ===")
        
        if summary_only:
            scores = [h['overall_score'] for h in history]
            self.io.print_info(f"Score progression: {' → '.join(f'{s:.3f}' for s in scores)}")
            self.io.print_info(f"Best score: {max(scores):.3f}")
            self.io.print_info(f"Latest score: {scores[-1]:.3f}")
        else:
            for i, eval_data in enumerate(history):
                self.io.print_info(f"\nIteration {eval_data['iteration']}:")
                self.io.print_info(f"  Score: {eval_data['overall_score']:.3f}")
                self.io.print_info(f"  Needs revision: {eval_data['needs_revision']}")
                if eval_data['suggestions']:
                    self.io.print_info(f"  Suggestions: {len(eval_data['suggestions'])} items")


def register_evaluation_commands():
    """Register evaluation commands with CLI"""
    return EvaluationCommands()