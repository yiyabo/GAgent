"""Centralized argument parser for CLI application."""

import argparse
from typing import Any, Dict, List, Optional

from .interfaces import ContextOptionsBuilder


class CLIParser:
    """Main argument parser for the CLI application."""

    def __init__(self, prog_name: str = "agent_cli"):
        self.parser = argparse.ArgumentParser(
            prog=prog_name,
            description="GLM Agent CLI - Intelligent task management and execution",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        self._setup_base_arguments()

    def _setup_base_arguments(self) -> None:
        """Setup common arguments used across commands."""
        # Core workflow arguments
        workflow_group = self.parser.add_argument_group("Core Workflow")
        workflow_group.add_argument("--goal", type=str, help="Goal to plan for (required for new plans)")
        workflow_group.add_argument("--plan-only", action="store_true", help="Only generate plan.md/plan.json and exit")
        workflow_group.add_argument(
            "--execute-only", action="store_true", help="Execute existing plan without creating new one"
        )
        workflow_group.add_argument("--yes", action="store_true", help="Non-interactive: auto-approve and auto-execute")
        workflow_group.add_argument("--no-open", action="store_true", help="Do not open editor for plan.md")

        # Plan configuration arguments
        plan_group = self.parser.add_argument_group("Plan Configuration")
        plan_group.add_argument("--title", type=str, help="Plan title for creation or execution")
        plan_group.add_argument("--sections", type=int, help="Preferred number of tasks (AI decides if not specified)")
        plan_group.add_argument("--style", type=str, help="Optional style (e.g., academic, concise)")
        plan_group.add_argument("--notes", type=str, help="Optional notes/hints for plan generation")
        plan_group.add_argument(
            "--output", type=str, default="output.md", help="Assembled output path (default: output.md)"
        )

        # Plan management arguments
        mgmt_group = self.parser.add_argument_group("Plan Management")
        mgmt_group.add_argument("--load-plan", type=str, help="Load existing plan by title")
        mgmt_group.add_argument("--list-plans", action="store_true", help="List all existing plans")

        # Execution control arguments
        exec_group = self.parser.add_argument_group("Execution Control")
        exec_group.add_argument(
            "--schedule",
            choices=["bfs", "dag", "postorder"],
            default="bfs",
            help="Task scheduling strategy (default: bfs)",
        )

        # Context management arguments
        context_group = self.parser.add_argument_group("Context Management")
        context_group.add_argument("--use-context", action="store_true", help="Enable context-aware execution")
        context_group.add_argument(
            "--include-deps", dest="include_deps", action="store_true", help="Include dependency tasks in context"
        )
        context_group.add_argument(
            "--exclude-deps", dest="include_deps", action="store_false", help="Exclude dependency tasks from context"
        )
        self.parser.set_defaults(include_deps=None)

        context_group.add_argument(
            "--include-plan", dest="include_plan", action="store_true", help="Include plan sibling tasks in context"
        )
        context_group.add_argument(
            "--exclude-plan", dest="include_plan", action="store_false", help="Exclude plan sibling tasks from context"
        )
        self.parser.set_defaults(include_plan=None)

        # Semantic retrieval arguments
        semantic_group = self.parser.add_argument_group("Semantic Retrieval")
        semantic_group.add_argument(
            "--semantic-k", dest="semantic_k", type=int, help="Number of semantically similar tasks to retrieve"
        )
        semantic_group.add_argument(
            "--min-similarity",
            dest="min_similarity",
            type=float,
            help="Minimum similarity threshold for retrieval (0.0-1.0)",
        )

        # Budget control arguments
        budget_group = self.parser.add_argument_group("Context Budget Control")
        budget_group.add_argument("--max-chars", dest="max_chars", type=int, help="Maximum character limit for context")
        budget_group.add_argument(
            "--per-section-max", dest="per_section_max", type=int, help="Maximum characters per section"
        )
        budget_group.add_argument("--strategy", choices=["truncate", "sentence"], help="Context truncation strategy")

        # Snapshot management arguments
        snapshot_group = self.parser.add_argument_group("Context Snapshots")
        snapshot_group.add_argument(
            "--save-snapshot",
            dest="save_snapshot",
            action="store_true",
            help="Save context snapshot for reproducibility",
        )
        snapshot_group.add_argument("--label", type=str, help="Label for the context snapshot")
        snapshot_group.add_argument("--list-snapshots", action="store_true", help="List context snapshots for a task")
        snapshot_group.add_argument("--export-snapshot", action="store_true", help="Export context snapshot to file")

        # Task rerun arguments
        rerun_group = self.parser.add_argument_group("Task Rerun")
        rerun_group.add_argument("--rerun-task", type=int, help="Rerun a specific task by ID")
        rerun_group.add_argument("--rerun-subtree", type=int, help="Rerun task and all its subtasks")
        rerun_group.add_argument(
            "--rerun-include-parent", action="store_true", help="Include parent task when rerunning subtree"
        )
        rerun_group.add_argument(
            "--rerun-interactive", action="store_true", help="Interactive task selection for rerun"
        )

        # Task management arguments
        task_group = self.parser.add_argument_group("Task Management")
        task_group.add_argument("--task-id", dest="task_id", type=int, help="Task ID for operations")
        task_group.add_argument("--list-children", action="store_true", help="List child tasks of a task")
        task_group.add_argument("--get-subtree", action="store_true", help="Get task subtree structure")
        task_group.add_argument("--move-task", action="store_true", help="Move task to new parent")
        task_group.add_argument(
            "--new-parent-id", dest="new_parent_id", type=int, help="New parent ID for task move operation"
        )

        # Index management arguments
        index_group = self.parser.add_argument_group("Global Index Management")
        index_group.add_argument("--index-preview", action="store_true", help="Preview generated INDEX.md content")
        index_group.add_argument("--index-export", type=str, help="Export INDEX.md to specified path")
        index_group.add_argument(
            "--index-run-root", action="store_true", help="Generate and persist INDEX.md with history"
        )

        # Embedding management arguments
        embed_group = self.parser.add_argument_group("Embedding Management")
        embed_group.add_argument("--generate-embeddings", action="store_true", help="Generate embeddings for all tasks")
        embed_group.add_argument("--embedding-stats", action="store_true", help="Show embedding service statistics")
        embed_group.add_argument(
            "--rebuild-embeddings", action="store_true", help="Rebuild all embeddings from scratch"
        )
        embed_group.add_argument(
            "--embedding-batch-size",
            dest="embedding_batch_size",
            type=int,
            default=10,
            help="Batch size for embedding generation (default: 10)",
        )

        # Evaluation system arguments
        eval_group = self.parser.add_argument_group("Evaluation System")
        eval_group.add_argument("--eval-config", type=int, help="Configure evaluation settings for a task")
        eval_group.add_argument("--eval-execute", type=int, help="Execute task with basic evaluation")
        eval_group.add_argument("--eval-llm", type=int, help="Execute task with LLM intelligent evaluation")
        eval_group.add_argument("--eval-multi-expert", type=int, help="Execute task with multi-expert evaluation")
        eval_group.add_argument("--eval-adversarial", type=int, help="Execute task with adversarial evaluation")
        eval_group.add_argument("--eval-history", type=int, help="View evaluation history for a task")
        eval_group.add_argument("--eval-override", type=int, help="Override evaluation result for a task")
        eval_group.add_argument("--eval-stats", action="store_true", help="Show evaluation system statistics")
        eval_group.add_argument("--eval-clear", type=int, help="Clear evaluation history for a task")
        eval_group.add_argument("--eval-batch", action="store_true", help="Run batch evaluation")
        eval_group.add_argument("--eval-supervision", action="store_true", help="Show evaluation supervision report")
        eval_group.add_argument(
            "--eval-supervision-config", action="store_true", help="Configure supervision thresholds"
        )

        # Evaluation configuration arguments
        eval_config_group = self.parser.add_argument_group("Evaluation Configuration")
        eval_config_group.add_argument(
            "--threshold", type=float, default=0.8, help="Quality threshold for evaluation (default: 0.8)"
        )
        eval_config_group.add_argument(
            "--max-iterations", type=int, default=3, help="Maximum iterations for evaluation (default: 3)"
        )
        eval_config_group.add_argument(
            "--max-rounds", type=int, default=3, help="Maximum rounds for adversarial evaluation (default: 3)"
        )
        eval_config_group.add_argument(
            "--improvement-threshold",
            type=float,
            default=0.1,
            help="Improvement threshold for adversarial evaluation (default: 0.1)",
        )
        eval_config_group.add_argument(
            "--experts", type=str, help="Comma-separated list of experts for multi-expert evaluation"
        )
        eval_config_group.add_argument("--verbose", action="store_true", help="Enable verbose evaluation output")
        eval_config_group.add_argument("--detailed", action="store_true", help="Show detailed evaluation information")
        eval_config_group.add_argument("--strict", action="store_true", help="Enable strict evaluation mode")
        eval_config_group.add_argument(
            "--domain-specific", action="store_true", help="Enable domain-specific evaluation"
        )

        # Supervision configuration arguments
        supervision_group = self.parser.add_argument_group("Supervision Configuration")
        supervision_group.add_argument("--min-accuracy", type=float, help="Minimum accuracy threshold for supervision")
        supervision_group.add_argument(
            "--min-consistency", type=float, help="Minimum consistency threshold for supervision"
        )
        supervision_group.add_argument(
            "--max-bias-risk", type=float, help="Maximum bias risk threshold for supervision"
        )
        supervision_group.add_argument(
            "--min-cache-hit-rate", type=float, help="Minimum cache hit rate threshold for supervision"
        )
        supervision_group.add_argument(
            "--max-error-rate", type=float, help="Maximum error rate threshold for supervision"
        )
        supervision_group.add_argument(
            "--max-evaluation-time", type=float, help="Maximum evaluation time threshold for supervision"
        )
        supervision_group.add_argument(
            "--min-confidence", type=float, help="Minimum confidence threshold for supervision"
        )

        # Database and cache management arguments
        db_group = self.parser.add_argument_group("Database and Cache Management")
        db_group.add_argument("--db-info", action="store_true", help="Show database information and statistics")
        db_group.add_argument(
            "--cache-stats", action="store_true", help="Show cache statistics and performance metrics"
        )
        db_group.add_argument("--clear-cache", action="store_true", help="Clear cache data")
        db_group.add_argument(
            "--cache-type",
            choices=["all", "evaluation", "embedding"],
            default="all",
            help="Type of cache to clear (default: all)",
        )
        db_group.add_argument("--cache-method", type=str, help="Clear cache for specific evaluation method")
        db_group.add_argument("--db-optimize", action="store_true", help="Optimize database performance")
        db_group.add_argument("--db-backup", action="store_true", help="Backup main database")
        db_group.add_argument("--backup-path", type=str, help="Custom backup file path")
        db_group.add_argument(
            "--db-analyze", action="store_true", help="Analyze database performance and provide recommendations"
        )
        db_group.add_argument(
            "--db-reset", action="store_true", help="Reset database (clear all data) - DESTRUCTIVE OPERATION"
        )

    def parse_args(self, args: Optional[List[str]] = None) -> argparse.Namespace:
        """Parse command line arguments."""
        return self.parser.parse_args(args)

    def add_subparser(self, name: str, help_text: str) -> argparse.ArgumentParser:
        """Add a subparser for command-specific arguments."""
        if not hasattr(self, "_subparsers"):
            self._subparsers = self.parser.add_subparsers(title="Commands", dest="command", help="Available commands")
        return self._subparsers.add_parser(name, help=help_text)


class DefaultContextOptionsBuilder(ContextOptionsBuilder):
    """Default implementation of context options builder."""

    def build_from_args(self, args: argparse.Namespace) -> Optional[Dict[str, Any]]:
        """Build context options from parsed arguments."""
        if not getattr(args, "use_context", False):
            return None

        options = {}

        # Basic context options
        if hasattr(args, "include_deps") and args.include_deps is not None:
            options["include_deps"] = bool(args.include_deps)
        if hasattr(args, "include_plan") and args.include_plan is not None:
            options["include_plan"] = bool(args.include_plan)

        # Semantic retrieval options
        if hasattr(args, "semantic_k") and args.semantic_k is not None:
            options["semantic_k"] = int(args.semantic_k)
        if hasattr(args, "min_similarity") and args.min_similarity is not None:
            options["min_similarity"] = float(args.min_similarity)

        # Budget control options
        if hasattr(args, "max_chars") and args.max_chars is not None:
            options["max_chars"] = int(args.max_chars)
        if hasattr(args, "per_section_max") and args.per_section_max is not None:
            options["per_section_max"] = int(args.per_section_max)
        if hasattr(args, "strategy") and args.strategy:
            options["strategy"] = str(args.strategy)

        # Snapshot options
        if hasattr(args, "save_snapshot") and args.save_snapshot:
            options["save_snapshot"] = True
        if hasattr(args, "label") and args.label:
            options["label"] = str(args.label)

        return options if options else None
