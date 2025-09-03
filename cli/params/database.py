"""Database and cache management parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Any, Dict, Optional


class DatabaseParamsHandler:
    """Handler for database and cache management parameters following SRP."""

    GROUP_NAME = "Database and Cache Management"

    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add database and cache management arguments to parser."""
        group = parser.add_argument_group(DatabaseParamsHandler.GROUP_NAME)

        # Database information commands
        group.add_argument("--db-info", action="store_true", help="Show database information and statistics")
        group.add_argument(
            "--db-analyze", action="store_true", help="Analyze database performance and provide recommendations"
        )
        group.add_argument(
            "--db-optimize", action="store_true", help="Optimize database performance (indexes, vacuum, analyze)"
        )

        # Database backup and restore commands
        group.add_argument("--db-backup", action="store_true", help="Backup main database")
        group.add_argument("--backup-path", type=str, help="Custom backup file path")
        group.add_argument("--db-reset", action="store_true", help="Reset database (DESTRUCTIVE - clears all data)")

        # Cache management commands
        cache_group = parser.add_argument_group("Cache Management")
        cache_group.add_argument(
            "--cache-stats", action="store_true", help="Show cache statistics and performance metrics"
        )
        cache_group.add_argument("--clear-cache", action="store_true", help="Clear cache data")
        cache_group.add_argument(
            "--cache-type",
            choices=["all", "evaluation", "embedding"],
            default="all",
            help="Type of cache to clear (default: all)",
        )
        cache_group.add_argument("--cache-method", type=str, help="Clear cache for specific evaluation method")

        # Task management commands
        task_group = parser.add_argument_group("Task Management")
        task_group.add_argument(
            "--task-id", dest="task_id", type=int, help="Task ID for operations requiring task reference"
        )
        task_group.add_argument(
            "--list-children", action="store_true", help="List child tasks of a specific task (requires --task-id)"
        )
        task_group.add_argument(
            "--get-subtree", action="store_true", help="Get task subtree structure (requires --task-id)"
        )
        task_group.add_argument(
            "--move-task", action="store_true", help="Move task to new parent (requires --task-id and --new-parent-id)"
        )
        task_group.add_argument(
            "--new-parent-id",
            dest="new_parent_id",
            type=int,
            help="New parent ID for task move operation (-1 for root level)",
        )

        # Rerun commands
        rerun_group = parser.add_argument_group("Task Rerun")
        rerun_group.add_argument("--rerun-task", type=int, help="Rerun a specific task by ID")
        rerun_group.add_argument("--rerun-subtree", type=int, help="Rerun task and all its subtasks by root task ID")
        rerun_group.add_argument(
            "--rerun-include-parent", action="store_true", help="Include parent task when rerunning subtree"
        )
        rerun_group.add_argument(
            "--rerun-interactive", action="store_true", help="Interactive task selection for rerun"
        )

    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract database parameter values from parsed args."""
        values = {}

        # Database operation commands
        db_commands = ["db_info", "db_analyze", "db_optimize", "db_backup", "db_reset"]
        for cmd in db_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True

        # Database configuration
        if hasattr(args, "backup_path") and getattr(args, "backup_path"):
            values["backup_path"] = getattr(args, "backup_path")

        # Cache operation commands
        cache_commands = ["cache_stats", "clear_cache"]
        for cmd in cache_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True

        # Cache configuration
        cache_attrs = ["cache_type", "cache_method"]
        for attr in cache_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value

        # Task management
        task_attrs = ["task_id", "new_parent_id"]
        for attr in task_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value

        task_commands = ["list_children", "get_subtree", "move_task"]
        for cmd in task_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True

        # Rerun operations
        rerun_attrs = ["rerun_task", "rerun_subtree"]
        for attr in rerun_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value

        rerun_flags = ["rerun_include_parent", "rerun_interactive"]
        for flag in rerun_flags:
            if hasattr(args, flag) and getattr(args, flag):
                values[flag] = True

        return values

    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate database parameter combinations."""
        # Task ID requirements
        task_id = values.get("task_id")

        # Operations requiring task_id
        task_ops = ["list_children", "get_subtree", "move_task"]
        for op in task_ops:
            if values.get(op) and not task_id:
                return False, f"--{op.replace('_', '-')} requires --task-id"

        # Move task specific requirements
        if values.get("move_task"):
            new_parent_id = values.get("new_parent_id")
            if new_parent_id is None:
                return False, "--move-task requires --new-parent-id"

        # Rerun validation
        rerun_task = values.get("rerun_task")
        rerun_subtree = values.get("rerun_subtree")
        if rerun_task and rerun_subtree:
            return False, "Cannot use --rerun-task and --rerun-subtree together"

        # Task ID range validation
        for attr in ["task_id", "rerun_task", "rerun_subtree"]:
            value = values.get(attr)
            if value is not None and value <= 0:
                return False, f"--{attr.replace('_', '-')} must be positive"

        # Parent ID validation (can be -1 for root)
        new_parent_id = values.get("new_parent_id")
        if new_parent_id is not None and new_parent_id < -1:
            return False, "--new-parent-id must be -1 (root) or positive"

        # Backup path validation
        backup_path = values.get("backup_path")
        if backup_path and (not backup_path.strip() or len(backup_path) > 255):
            return False, "Backup path must be non-empty and under 255 characters"

        # Cache method validation
        cache_method = values.get("cache_method")
        if cache_method and not cache_method.strip():
            return False, "Cache method must be non-empty"

        return True, None

    @staticmethod
    def has_database_operation(args) -> bool:
        """Check if any database operation is requested."""
        db_ops = ["db_info", "cache_stats", "clear_cache", "db_optimize", "db_backup", "db_analyze", "db_reset"]
        return any(hasattr(args, attr) and getattr(args, attr) for attr in db_ops)

    @staticmethod
    def has_rerun_operation(args) -> bool:
        """Check if any rerun operation is requested."""
        rerun_ops = ["rerun_task", "rerun_subtree", "rerun_interactive"]
        return any(hasattr(args, attr) and getattr(args, attr) for attr in rerun_ops)
