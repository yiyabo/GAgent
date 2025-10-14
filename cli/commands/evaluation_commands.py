"""
评估系统CLI命令 - 统一使用API调用

这个版本将所有的评估功能都通过API调用，而不是直接调用服务层。
这确保了CLI和API的一致性，并简化了维护工作。
"""

import os
import sys
from argparse import Namespace
from typing import Any, Dict, Optional

# Add app path for imports when running from tests
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from typing import List
from ..utils.api_client import get_api_client, APIClientError
from .base import MultiCommand


class EvaluationCommands(MultiCommand):
    """评估系统CLI命令 - 统一API调用"""

    def __init__(self):
        super().__init__()
        self.api_client = get_api_client()

    @property
    def name(self) -> str:
        return "evaluation"

    @property
    def description(self) -> str:
        return "Content evaluation and quality management commands (API-driven)"

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
        self.io.print_info("Available evaluation operations (API-driven):")
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
        return 0

    def handle_config(self, args: Namespace) -> int:
        """Handle evaluation configuration via API"""
        task_id = args.eval_config
        if not task_id:
            self.io.print_error("Task ID is required for configuration")
            return 1

        try:
            # Build configuration payload
            config = {
                "quality_threshold": getattr(args, "threshold", 0.8),
                "max_iterations": getattr(args, "max_iterations", 3),
                "domain_specific": getattr(args, "domain_specific", False),
                "strict_mode": getattr(args, "strict", False),
            }

            # Add optional parameters
            if hasattr(args, "dimensions") and args.dimensions:
                config["evaluation_dimensions"] = args.dimensions
            if hasattr(args, "weights") and args.weights:
                import json
                config["custom_weights"] = json.loads(args.weights)

            # Call API
            result = self.api_client.post(f"/tasks/{task_id}/evaluation/config", json_data=config)

            self.io.print_success(f"Evaluation configuration updated for task {task_id}")
            self._display_config(result.get("config", {}))
            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to configure evaluation: {e}")
            return 1
        except ValueError as e:
            self.io.print_error(f"Configuration error: {e}")
            return 1

    def handle_execute(self, args: Namespace) -> int:
        """Handle basic task execution with evaluation via API"""
        task_id = args.eval_execute
        if not task_id:
            self.io.print_error("Task ID is required for execution")
            return 1

        try:
            # Build execution payload
            payload = {
                "max_iterations": getattr(args, "max_iterations", 3),
                "quality_threshold": getattr(args, "threshold", 0.8),
                "use_context": getattr(args, "use_context", False),
            }

            # Add context options if specified
            if hasattr(args, "context_options"):
                payload["context_options"] = args.context_options

            self.io.print_info(f"Executing task {task_id} with evaluation...")
            self.io.print_info(f"Quality threshold: {payload['quality_threshold']}")
            self.io.print_info(f"Max iterations: {payload['max_iterations']}")

            # Call API
            result = self.api_client.post(f"/tasks/{task_id}/execute/with-evaluation", json_data=payload)

            # Display results
            self._display_execution_result(result)
            return 0 if result.get("status") in ["done", "needs_review"] else 1

        except APIClientError as e:
            self.io.print_error(f"Execution failed: {e}")
            return 1

    def handle_llm_execute(self, args: Namespace) -> int:
        """Execute task with LLM intelligent evaluation via API"""
        task_id = args.eval_llm
        if not task_id:
            self.io.print_error("Task ID is required for LLM evaluation")
            return 1

        try:
            # Build execution payload for LLM evaluation
            payload = {
                "title": getattr(args, "title", None),  # For plan-based execution
                "target_task_id": task_id,
                "use_context": getattr(args, "use_context", True),
                "enable_evaluation": True,
                "evaluation_mode": "llm",
                "evaluation_options": {
                    "max_iterations": getattr(args, "max_iterations", 3),
                    "quality_threshold": getattr(args, "threshold", 0.8),
                }
            }

            self.io.print_info(f"🧠 Executing task {task_id} with LLM intelligent evaluation")
            self.io.print_info(f"   Quality threshold: {payload['evaluation_options']['quality_threshold']}")
            self.io.print_info(f"   Max iterations: {payload['evaluation_options']['max_iterations']}")

            # Call the unified run API with LLM evaluation
            result = self.api_client.post("/run", json_data=payload)

            # Extract task-specific result
            task_result = self._extract_task_result(result, task_id)
            if task_result:
                self._display_llm_evaluation_result(task_result)
                return 0 if task_result.get("status") in ["done", "completed"] else 1
            else:
                self.io.print_error("Failed to get task-specific result")
                return 1

        except APIClientError as e:
            self.io.print_error(f"LLM evaluation execution failed: {e}")
            return 1

    def handle_multi_expert_execute(self, args: Namespace) -> int:
        """Execute task with multi-expert evaluation via API"""
        task_id = args.eval_multi_expert
        if not task_id:
            self.io.print_error("Task ID is required for multi-expert evaluation")
            return 1

        try:
            # Build execution payload for multi-expert evaluation
            payload = {
                "title": getattr(args, "title", None),
                "target_task_id": task_id,
                "use_context": getattr(args, "use_context", True),
                "enable_evaluation": True,
                "evaluation_mode": "multi_expert",
                "evaluation_options": {
                    "max_iterations": getattr(args, "max_iterations", 3),
                    "quality_threshold": getattr(args, "threshold", 0.8),
                }
            }

            # Add expert selection if specified
            if hasattr(args, "experts") and args.experts:
                experts = [e.strip() for e in args.experts.split(",") if e.strip()]
                payload["selected_experts"] = experts
                self.io.print_info(f"🎭 Executing task {task_id} with selected experts: {', '.join(experts)}")
            else:
                self.io.print_info(f"🎭 Executing task {task_id} with all available experts")

            # Call API
            result = self.api_client.post("/run", json_data=payload)

            # Extract and display result
            task_result = self._extract_task_result(result, task_id)
            if task_result:
                self._display_multi_expert_result(task_result)
                return 0 if task_result.get("status") in ["done", "completed"] else 1
            else:
                self.io.print_error("Failed to get task-specific result")
                return 1

        except APIClientError as e:
            self.io.print_error(f"Multi-expert evaluation execution failed: {e}")
            return 1

    def handle_adversarial_execute(self, args: Namespace) -> int:
        """Execute task with adversarial evaluation via API"""
        task_id = args.eval_adversarial
        if not task_id:
            self.io.print_error("Task ID is required for adversarial evaluation")
            return 1

        try:
            # Build execution payload for adversarial evaluation
            payload = {
                "title": getattr(args, "title", None),
                "target_task_id": task_id,
                "use_context": getattr(args, "use_context", True),
                "enable_evaluation": True,
                "evaluation_mode": "adversarial",
                "evaluation_options": {
                    "max_iterations": getattr(args, "max_rounds", 3),
                    "quality_threshold": getattr(args, "improvement_threshold", 0.1),
                }
            }

            self.io.print_info(f"⚔️  Executing task {task_id} with adversarial evaluation")
            self.io.print_info(f"   Max rounds: {payload['evaluation_options']['max_iterations']}")

            # Call API
            result = self.api_client.post("/run", json_data=payload)

            # Extract and display result
            task_result = self._extract_task_result(result, task_id)
            if task_result:
                self._display_adversarial_result(task_result)
                return 0 if task_result.get("status") in ["done", "completed"] else 1
            else:
                self.io.print_error("Failed to get task-specific result")
                return 1

        except APIClientError as e:
            self.io.print_error(f"Adversarial evaluation execution failed: {e}")
            return 1

    def handle_history(self, args: Namespace) -> int:
        """Handle evaluation history viewing via API"""
        task_id = args.eval_history
        if not task_id:
            self.io.print_error("Task ID is required for history")
            return 1

        try:
            latest = getattr(args, "latest", False)

            if latest:
                # Get latest evaluation
                result = self.api_client.get(f"/tasks/{task_id}/evaluation/latest")
                evaluation = result.get("evaluation")
                if evaluation:
                    self._display_single_evaluation(evaluation)
                else:
                    self.io.print_warning(f"No evaluation history found for task {task_id}")
            else:
                # Get full history
                result = self.api_client.get(f"/tasks/{task_id}/evaluation/history")
                history = result.get("history", [])
                if history:
                    self._display_evaluation_history(history, getattr(args, "summary", False))
                else:
                    self.io.print_warning(f"No evaluation history found for task {task_id}")

            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to get evaluation history: {e}")
            return 1

    def handle_override(self, args: Namespace) -> int:
        """Handle evaluation override via API"""
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

            # Build override payload
            payload = {
                "human_score": score,
                "human_feedback": getattr(args, "feedback", ""),
                "override_reason": getattr(args, "reason", ""),
            }

            # Call API
            result = self.api_client.post(f"/tasks/{task_id}/evaluation/override", json_data=payload)

            self.io.print_success(f"Evaluation override applied for task {task_id}")
            self.io.print_info(f"  Previous score: {result.get('previous_score', 0):.3f}")
            self.io.print_info(f"  New score: {result.get('new_score', 0):.3f}")

            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to override evaluation: {e}")
            return 1

    def handle_stats(self, args: Namespace) -> int:
        """Handle evaluation statistics via API"""
        try:
            result = self.api_client.get("/evaluation/stats")
            stats = result.get("evaluation_stats", {})

            self.io.print_info("=== Evaluation System Statistics (via API) ===")
            self.io.print_info(f"Total evaluations: {stats.get('total_evaluations', 0)}")
            self.io.print_info(f"Average score: {stats.get('average_score', 0):.3f}")
            self.io.print_info(f"Average iterations: {stats.get('average_iterations', 0):.1f}")
            self.io.print_info(f"Max iterations used: {stats.get('max_iterations_used', 0)}")

            detailed = getattr(args, "detailed", False)
            if detailed and stats.get("quality_distribution"):
                self.io.print_info("\nQuality Distribution:")
                for tier, count in stats["quality_distribution"].items():
                    total = stats.get("total_evaluations", 1)
                    percentage = (count / total * 100) if total > 0 else 0
                    self.io.print_info(f"  {tier}: {count} ({percentage:.1f}%)")

            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to get statistics: {e}")
            return 1

    def handle_clear(self, args: Namespace) -> int:
        """Handle evaluation history clearing via API"""
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

            # Call API
            result = self.api_client.delete(f"/tasks/{task_id}/evaluation/history")
            
            if result.get("history_cleared"):
                self.io.print_success(f"Evaluation history cleared for task {task_id}")
            else:
                self.io.print_warning("History clearing status unclear")

            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to clear history: {e}")
            return 1

    def handle_batch(self, args: Namespace) -> int:
        """Handle batch evaluation via API"""
        try:
            task_ids = getattr(args, "task_ids", None)
            if not task_ids:
                self.io.print_error("No task IDs provided")
                return 1

            threshold = getattr(args, "threshold", 0.8)
            max_iterations = getattr(args, "max_iterations", 3)
            use_context = getattr(args, "use_context", False)

            self.io.print_info(f"Starting batch evaluation of {len(task_ids)} tasks via API")

            # Use the new batch evaluation API endpoint
            payload = {
                "task_ids": task_ids,
                "max_iterations": max_iterations,
                "quality_threshold": threshold,
                "use_context": use_context,
            }

            result = self.api_client.post("/evaluation/batch", json_data=payload)
            
            # Extract results
            batch_results = result.get("batch_results", [])
            summary = result.get("summary", {})
            configuration = result.get("configuration", {})

            # Display individual results
            for task_result in batch_results:
                task_id = task_result.get("task_id")
                status = task_result.get("status", "unknown")
                
                if status == "failed":
                    error = task_result.get("error", "Unknown error")
                    self.io.print_error(f"  Task {task_id}: FAILED - {error}")
                else:
                    score = task_result.get("final_score", 0.0)
                    iterations = task_result.get("iterations", 0)
                    execution_time = task_result.get("execution_time", 0.0)
                    self.io.print_info(f"  Task {task_id}: {status} (score: {score:.3f}, iterations: {iterations}, time: {execution_time:.2f}s)")

            # Display summary
            total = summary.get("total", 0)
            successful = summary.get("successful", 0) 
            failed = summary.get("failed", 0)
            success_rate = summary.get("success_rate", 0.0)

            self.io.print_success(f"\nBatch evaluation completed via API:")
            self.io.print_info(f"  Total tasks: {total}")
            self.io.print_info(f"  Successful: {successful}")
            self.io.print_info(f"  Failed: {failed}")
            self.io.print_info(f"  Success rate: {success_rate:.1%}")
            
            # Display configuration used
            self.io.print_info(f"\nConfiguration used:")
            self.io.print_info(f"  Quality threshold: {configuration.get('quality_threshold', 0.8)}")
            self.io.print_info(f"  Max iterations: {configuration.get('max_iterations', 3)}")
            self.io.print_info(f"  Use context: {configuration.get('use_context', False)}")

            return 0 if failed == 0 else 1

        except APIClientError as e:
            self.io.print_error(f"Batch evaluation failed: {e}")
            return 1

    def handle_supervision(self, args: Namespace) -> int:
        """Handle evaluation supervision report via API"""
        try:
            detailed = getattr(args, "detailed", False)
            
            self.io.print_info("🔍 Generating evaluation supervision report via API...")
            
            # Call API
            result = self.api_client.get("/evaluation/supervision")
            supervision_report = result.get("supervision_report", {})
            
            # Display system health
            system_health = supervision_report.get("system_health", {})
            overall_score = system_health.get("overall_score", 0.0)
            status = system_health.get("status", "unknown")

            status_color = "success" if status == "healthy" else "warning" if status == "degraded" else "error"

            self.io.print_info("\n=== 评估系统监督报告 (via API) ===")
            getattr(self.io, f"print_{status_color}")(f"系统健康状态: {status.upper()}")
            self.io.print_info(f"整体健康评分: {overall_score:.3f}")
            self.io.print_info(f"报告时间: {result.get('timestamp', 'N/A')}")

            # Current metrics
            current_metrics = supervision_report.get("current_metrics", {})
            if current_metrics:
                self.io.print_info("\n📊 当前质量指标:")
                for metric_name, metric_data in current_metrics.items():
                    value = metric_data.get("value", 0.0)
                    status_metric = metric_data.get("status", "unknown")
                    threshold = metric_data.get("threshold", 0.0)

                    metric_color = "success" if status_metric == "good" else "warning" if status_metric == "warning" else "error"
                    self.io.print_info(f"  {metric_name}: {value:.3f} (阈值: {threshold:.3f}) [{status_metric}]")

            # Performance summary (if detailed)
            if detailed:
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

            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to get supervision report: {e}")
            return 1

    def handle_supervision_config(self, args: Namespace) -> int:
        """Handle supervision configuration via API"""
        try:
            # Parse threshold updates
            config = {}

            # Check for common threshold arguments
            threshold_fields = [
                ("min_accuracy", "min_accuracy"),
                ("min_consistency", "min_consistency"), 
                ("max_bias_risk", "max_bias_risk"),
                ("min_cache_hit_rate", "min_cache_hit_rate"),
                ("max_error_rate", "max_error_rate"),
                ("max_evaluation_time", "max_evaluation_time"),
                ("min_confidence", "min_confidence")
            ]

            for attr_name, config_key in threshold_fields:
                if hasattr(args, attr_name) and getattr(args, attr_name) is not None:
                    config[config_key] = float(getattr(args, attr_name))

            # Call API
            result = self.api_client.post("/evaluation/supervision/config", json_data=config)
            
            if result.get("action") == "get_config":
                # Show current configuration
                current_config = result.get("current_config", {})
                available_thresholds = result.get("available_thresholds", [])
                
                self.io.print_info("=== 当前监督系统配置 (via API) ===")
                if current_config:
                    self.io.print_info("\n当前阈值:")
                    for threshold_name, value in current_config.items():
                        self.io.print_info(f"  {threshold_name}: {value}")
                
                self.io.print_info("\n可配置的阈值:")
                for threshold in available_thresholds:
                    self.io.print_info(f"  --{threshold.replace('_', '-')} <value>")
                    
            elif result.get("action") == "update_config":
                # Show update results
                success = result.get("success", False)
                updated_thresholds = result.get("updated_thresholds", {})
                
                if success:
                    self.io.print_success("监督系统阈值更新成功 (via API)!")
                    self.io.print_info("更新的阈值:")
                    for threshold_name, value in updated_thresholds.items():
                        self.io.print_info(f"  {threshold_name}: {value}")
                else:
                    self.io.print_error("监督系统阈值更新失败")
                    return 1

            return 0

        except APIClientError as e:
            self.io.print_error(f"Failed to configure supervision: {e}")
            return 1

    # Helper methods for result display
    def _display_config(self, config: Dict[str, Any]):
        """Display evaluation configuration"""
        self.io.print_info(f"  Quality threshold: {config.get('quality_threshold', 0.8)}")
        self.io.print_info(f"  Max iterations: {config.get('max_iterations', 3)}")
        self.io.print_info(f"  Strict mode: {config.get('strict_mode', False)}")
        self.io.print_info(f"  Domain-specific: {config.get('domain_specific', False)}")

        dimensions = config.get("evaluation_dimensions")
        if dimensions:
            self.io.print_info(f"  Dimensions: {', '.join(dimensions)}")
        
        custom_weights = config.get("custom_weights")
        if custom_weights:
            self.io.print_info(f"  Custom weights: {custom_weights}")

    def _display_execution_result(self, result: Dict[str, Any]):
        """Display task execution result"""
        task_id = result.get("task_id")
        status = result.get("status", "unknown")
        iterations = result.get("iterations", 0)
        execution_time = result.get("execution_time", 0.0)
        final_score = result.get("final_score", 0.0)

        status_color = (
            "success" if status == "done" else "warning" if status == "needs_review" else "error"
        )

        self.io.print_info(f"\n=== Execution Result (Task {task_id}) ===")
        getattr(self.io, f"print_{status_color}")(f"Status: {status.upper()}")
        self.io.print_info(f"Final score: {final_score:.3f}")
        self.io.print_info(f"Iterations: {iterations}")
        self.io.print_info(f"Execution time: {execution_time:.2f}s")

        evaluation = result.get("evaluation")
        if evaluation:
            dimensions = evaluation.get("dimensions", {})
            if dimensions:
                self.io.print_info("\nDimension Scores:")
                for dim, score in dimensions.items():
                    if score and score > 0:  # Only show evaluated dimensions
                        self.io.print_info(f"  {dim}: {score:.3f}")

            suggestions = evaluation.get("suggestions", [])
            if suggestions:
                self.io.print_info("\nSuggestions:")
                for suggestion in suggestions:
                    self.io.print_info(f"  • {suggestion}")

    def _display_llm_evaluation_result(self, result: Dict[str, Any]):
        """Display LLM evaluation result"""
        self.io.print_success("✅ LLM evaluation completed!")
        self.io.print_info(f"   Final status: {result.get('status')}")
        
        evaluation = result.get("evaluation", {})
        if evaluation:
            score = evaluation.get("score", 0.0)
            iterations = result.get("iterations", 0)
            self.io.print_info(f"   Final score: {score:.3f}")
            self.io.print_info(f"   Iterations: {iterations}")

            # Show dimension scores if available
            dimensions = evaluation.get("dimensions", {})
            if dimensions:
                self.io.print_info(f"   📊 Dimension scores:")
                for dim, score in dimensions.items():
                    if score and score > 0:
                        self.io.print_info(f"      {dim}: {score:.3f}")

            suggestions = evaluation.get("suggestions", [])
            if suggestions:
                self.io.print_info(f"   💡 Improvement suggestions:")
                for i, suggestion in enumerate(suggestions[:3], 1):
                    self.io.print_info(f"      {i}. {suggestion}")

    def _display_multi_expert_result(self, result: Dict[str, Any]):
        """Display multi-expert evaluation result"""
        self.io.print_success(f"✅ Multi-expert evaluation completed!")
        self.io.print_info(f"   Final status: {result.get('status')}")
        
        evaluation = result.get("evaluation", {})
        if evaluation:
            score = evaluation.get("score", 0.0)
            self.io.print_info(f"   Consensus score: {score:.3f}")

        # Extract metadata if available
        artifacts = result.get("artifacts", {})
        if isinstance(artifacts, dict):
            expert_evaluations = artifacts.get("expert_evaluations", {})
            if expert_evaluations:
                self.io.print_info(f"\n   👥 Expert Scores:")
                for expert_name, evaluation in expert_evaluations.items():
                    expert_role = evaluation.get("expert_role", expert_name)
                    overall_score = evaluation.get("overall_score", 0)
                    confidence = evaluation.get("confidence_level", 0)
                    self.io.print_info(f"      {expert_role}: {overall_score:.3f} (信心度: {confidence:.2f})")

    def _display_adversarial_result(self, result: Dict[str, Any]):
        """Display adversarial evaluation result"""
        self.io.print_success(f"✅ Adversarial evaluation completed!")
        self.io.print_info(f"   Final status: {result.get('status')}")
        
        evaluation = result.get("evaluation", {})
        if evaluation:
            score = evaluation.get("score", 0.0)
            rounds = result.get("iterations", 0)
            self.io.print_info(f"   Robustness score: {score:.3f}")
            self.io.print_info(f"   Rounds completed: {rounds}")

        # Show adversarial insights
        artifacts = result.get("artifacts", {})
        if isinstance(artifacts, dict):
            adversarial_effectiveness = artifacts.get("adversarial_effectiveness", 0.0)
            self.io.print_info(f"\n   ⚔️  Adversarial Analysis:")
            self.io.print_info(f"      Adversarial effectiveness: {adversarial_effectiveness:.3f}")

    def _display_single_evaluation(self, evaluation: Dict[str, Any]):
        """Display a single evaluation"""
        self.io.print_info(f"=== Evaluation (Iteration {evaluation.get('iteration', 0)}) ===")
        self.io.print_info(f"Overall score: {evaluation.get('overall_score', 0):.3f}")
        self.io.print_info(f"Needs revision: {evaluation.get('needs_revision', False)}")
        self.io.print_info(f"Timestamp: {evaluation.get('timestamp', 'unknown')}")

        dimension_scores = evaluation.get("dimension_scores", {})
        if dimension_scores:
            self.io.print_info("\nDimension Scores:")
            for dim, score in dimension_scores.items():
                self.io.print_info(f"  {dim}: {score:.3f}")

        suggestions = evaluation.get("suggestions", [])
        if suggestions:
            self.io.print_info("\nSuggestions:")
            for suggestion in suggestions:
                self.io.print_info(f"  • {suggestion}")

    def _display_evaluation_history(self, history: List[Dict[str, Any]], summary_only: bool = False):
        """Display evaluation history"""
        self.io.print_info(f"=== Evaluation History ({len(history)} iterations) ===")

        if summary_only:
            scores = [h.get("overall_score", 0) for h in history]
            self.io.print_info(f"Score progression: {' → '.join(f'{s:.3f}' for s in scores)}")
            self.io.print_info(f"Best score: {max(scores):.3f}" if scores else "N/A")
            self.io.print_info(f"Latest score: {scores[-1]:.3f}" if scores else "N/A")
        else:
            for eval_data in history:
                iteration = eval_data.get("iteration", 0)
                score = eval_data.get("overall_score", 0)
                needs_revision = eval_data.get("needs_revision", False)
                suggestions = eval_data.get("suggestions", [])
                
                self.io.print_info(f"\nIteration {iteration}:")
                self.io.print_info(f"  Score: {score:.3f}")
                self.io.print_info(f"  Needs revision: {needs_revision}")
                if suggestions:
                    self.io.print_info(f"  Suggestions: {len(suggestions)} items")

    def _extract_task_result(self, run_result: Dict[str, Any], task_id: int) -> Optional[Dict[str, Any]]:
        """Extract task-specific result from run API response"""
        results = run_result.get("results", [])
        for result in results:
            if result.get("id") == task_id:
                return result
        return None


def register_evaluation_commands():
    """Register evaluation commands with CLI"""
    return EvaluationCommands()
