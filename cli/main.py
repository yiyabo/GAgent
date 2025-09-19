"""Main CLI application entry point."""

import sys
from typing import List, Optional

from .commands import PlanCommands, RerunCommands
from .commands.chat_commands_refactored import ChatCommandsRefactored
from .commands.database_commands import DatabaseCommands
from .commands.evaluation_commands_refactored import EvaluationCommands as EvaluationCommandsRefactored
from .commands.memory_commands import MemoryCommands
from .commands.task_commands_new import TaskCommands
from .interfaces import CLIApplication, CLICommand
from .parser_v2 import ModularCLIParser
from .utils import FileUtils, IOUtils

try:
    from ..app.errors import BusinessError, ErrorCode, ValidationError
    from .error_handler import CLIErrorContext, CLIErrorHandler, handle_cli_exception
except ImportError:
    from app.errors import BusinessError, ErrorCode, ValidationError
    from cli.error_handler import CLIErrorContext, CLIErrorHandler, handle_cli_exception


class ModernCLIApp(CLIApplication):
    """Modern CLI application with refactored modular parameter handling."""

    def __init__(self):
        self.parser = ModularCLIParser()
        self.commands: List[CLICommand] = []
        self.io = IOUtils()
        self.error_handler = CLIErrorHandler(verbose=False, chinese=False)

        # Initialize UTF-8 encoding
        FileUtils.ensure_utf8_encoding()

        # Register built-in commands
        self._register_builtin_commands()

    def _register_builtin_commands(self):
        """Register all built-in commands."""
        self.register_command(RerunCommands())
        self.register_command(PlanCommands())
        # Use the refactored evaluation commands (API-driven)
        self.register_command(EvaluationCommandsRefactored())
        # Add new task management commands
        self.register_command(TaskCommands())
        self.register_command(DatabaseCommands())
        self.register_command(MemoryCommands())
        # Use the refactored chat commands (API-driven)
        self.register_command(ChatCommandsRefactored())

    def register_command(self, command: CLICommand) -> None:
        """Register a command with the application."""
        self.commands.append(command)

    def run(self, args: Optional[List[str]] = None) -> int:
        """Run the CLI application with refactored parameter handling."""
        try:
            # Parse arguments using modular parser
            parsed_args = self.parser.parse_args(args)

            # Extract and validate parameters
            all_params, validation_error = self.parser.extract_and_validate_params(parsed_args)
            if validation_error:
                # Use friendly error handling
                validation_err = ValidationError(
                    message="Command line parameter validation failed",
                    error_code=ErrorCode.SCHEMA_VALIDATION_FAILED,
                    context={"validation_details": validation_error},
                    suggestions=[
                        "Check command line parameter format",
                        "Use --help to view parameter description",
                        "Ensure all required parameters are provided",
                    ],
                )
                error_info = self.error_handler.handle_error(validation_err)
                self.error_handler.print_error(error_info)
                return error_info.exit_code

            # Determine operation type using modular approach
            operation_type = self.parser.determine_operation_type(parsed_args)

            return self._execute_operation_with_error_handling(parsed_args, all_params, operation_type)

        except SystemExit as e:
            return e.code or 0
        except Exception as e:
            return handle_cli_exception(e, verbose=True)

    def _execute_operation_with_error_handling(self, args, all_params: dict, operation_type: str) -> int:
        """Execute operation with comprehensive error handling."""
        with CLIErrorContext(f"{operation_type} operation", verbose=True, chinese=True):
            return self._execute_operation(args, all_params, operation_type)

    def _execute_operation(self, args, all_params: dict, operation_type: str) -> int:
        """Execute operation based on determined type (simplified using modular approach)."""
        # Route to appropriate command based on operation type
        if operation_type == "database":
            db_cmd = self._get_command_by_name("database")
            if db_cmd:
                return db_cmd.execute(args)

        elif operation_type == "memory":
            memory_cmd = self._get_command_by_name("memory")
            if memory_cmd:
                return memory_cmd.execute(args)

        elif operation_type == "evaluation":
            eval_cmd = self._get_command_by_name("evaluation")
            if eval_cmd:
                return eval_cmd.execute(args)

        elif operation_type == "rerun":
            rerun_cmd = self._get_command_by_name("rerun")
            if rerun_cmd:
                return rerun_cmd.execute(args)

        elif operation_type == "plan":
            plan_cmd = self._get_command_by_name("plan")
            if plan_cmd:
                return plan_cmd.execute(args)

        elif operation_type == "chat":
            chat_cmd = self._get_command_by_name("chat")
            if chat_cmd:
                return chat_cmd.execute(args)

        elif operation_type == "utility":
            return self._handle_utility_operations(args)

        elif operation_type == "help":
            return self._show_help_guidance()

        # Fallback - Use friendly error handling
        raise BusinessError(
            message=f"Unknown operation type: {operation_type}",
            error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
            context={"operation_type": operation_type},
            suggestions=[
                "Check if command syntax is correct",
                "Use --help to view available command options",
                "Ensure operation name is spelled correctly",
            ],
        )

    def _get_command_by_name(self, name: str) -> Optional[CLICommand]:
        """Get a command by its name."""
        for cmd in self.commands:
            if cmd.name == name:
                return cmd
        return None

    def _handle_utility_operations(self, args) -> int:
        """Handle utility operations like snapshots, index, embeddings."""
        # Snapshot operations
        if getattr(args, "list_snapshots", False):
            return self._handle_list_snapshots(args)

        if getattr(args, "export_snapshot", False):
            return self._handle_export_snapshot(args)

        # Index operations
        if getattr(args, "index_preview", False):
            return self._handle_index_preview(args)

        if getattr(args, "index_export", None):
            return self._handle_index_export(args)

        if getattr(args, "index_run_root", False):
            return self._handle_index_run_root(args)

        # Task hierarchy operations
        if getattr(args, "list_children", False):
            return self._handle_list_children(args)

        if getattr(args, "get_subtree", False):
            return self._handle_get_subtree(args)

        if getattr(args, "move_task", False):
            return self._handle_move_task(args)

        # Embedding operations
        if getattr(args, "generate_embeddings", False):
            return self._handle_generate_embeddings(args)

        if getattr(args, "embedding_stats", False):
            return self._handle_embedding_stats(args)

        if getattr(args, "rebuild_embeddings", False):
            return self._handle_rebuild_embeddings(args)

        # Benchmark operation
        if getattr(args, "benchmark", False):
            return self._handle_benchmark(args)

        # No specific operation found - show help guidance
        return self._show_help_guidance()

    def _handle_list_snapshots(self, args) -> int:
        """Handle --list-snapshots operation."""
        task_id = getattr(args, "task_id", None)
        if not task_id:
            self.io.print_error("--task-id is required for --list-snapshots")
            return 1

        try:
            import os, requests
            base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")
            response = requests.get(f"{base_url}/tasks/{task_id}/context/snapshots")

            if response.status_code == 200:
                snapshots = response.json().get("snapshots", [])
                if not snapshots:
                    self.io.print_info(f"No snapshots found for task {task_id}")
                    return 0

                self.io.print_section(f"Context Snapshots for Task {task_id}")
                for snapshot in snapshots:
                    label = snapshot.get("label", "unlabeled")
                    created = snapshot.get("created_at", "unknown")
                    sections = len(snapshot.get("sections", []))
                    print(f"  • {label} (created: {created}, sections: {sections})")

                return 0
            else:
                self.io.print_error(f"Failed to list snapshots: HTTP {response.status_code}")
                return 1

        except Exception as e:
            self.io.print_error(f"Failed to list snapshots: {e}")
            return 1

    def _handle_export_snapshot(self, args) -> int:
        """Handle --export-snapshot operation."""
        task_id = getattr(args, "task_id", None)
        label = getattr(args, "label", None)

        if not task_id or not label:
            self.io.print_error("--task-id and --label are required for --export-snapshot")
            return 1

        try:
            import os, requests
            base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")
            response = requests.get(f"{base_url}/tasks/{task_id}/context/snapshots/{label}")

            if response.status_code == 200:
                snapshot_data = response.json()
                combined = snapshot_data.get("combined", "")

                filename = f"snapshot_task{task_id}_{label}.md"
                if FileUtils.write_file_safe(filename, combined):
                    self.io.print_success(f"Snapshot exported to {filename}")
                    return 0
                else:
                    return 1
            else:
                self.io.print_error(f"Failed to export snapshot: HTTP {response.status_code}")
                return 1

        except Exception as e:
            self.io.print_error(f"Failed to export snapshot: {e}")
            return 1

    def _handle_index_preview(self, args) -> int:
        """Handle --index-preview operation."""
        try:
            from app.services.context.index_root import generate_index

            result = generate_index()
            content = result.get("content", "")
            resolved_path = result.get("path", "INDEX.md")

            print(f"=== INDEX preview (resolved path: {resolved_path}) ===\n")
            print(content)
            return 0

        except Exception as e:
            self.io.print_error(f"Index preview failed: {e}")
            return 1

    def _handle_index_export(self, args) -> int:
        """Handle --index-export operation."""
        export_path = args.index_export

        try:
            from app.services.index_root import generate_index

            result = generate_index()
            content = result.get("content", "")

            if FileUtils.write_file_safe(export_path, content):
                self.io.print_success(f"Generated INDEX.md exported to {export_path}")
                return 0
            else:
                return 1

        except Exception as e:
            self.io.print_error(f"Index export failed: {e}")
            return 1

    def _handle_index_run_root(self, args) -> int:
        """Handle --index-run-root operation."""
        try:
            from app.services.index_root import generate_index, write_index

            result = generate_index()
            content = result.get("content", "")
            resolved_path = result.get("path", "INDEX.md")
            meta = result.get("meta", {})

            path_written = write_index(content, path=resolved_path, meta=meta)
            self.io.print_success(f"INDEX.md regenerated at {path_written}. History updated.")
            return 0

        except Exception as e:
            self.io.print_error(f"Index run-root failed: {e}")
            return 1

    def _handle_list_children(self, args) -> int:
        """Handle --list-children operation."""
        task_id = getattr(args, "task_id", None)
        if not task_id:
            self.io.print_error("--task-id is required for --list-children")
            return 1

        try:
            from app.database import init_db
            from app.repository.tasks import SqliteTaskRepository

            init_db()
            repo = SqliteTaskRepository()
            children = repo.get_children(task_id)

            if not children:
                self.io.print_info(f"No children found for task {task_id}")
                return 0

            self.io.print_section(f"Children of Task {task_id}")
            self.io.print_task_list(children)
            return 0

        except Exception as e:
            self.io.print_error(f"List children failed: {e}")
            return 1

    def _handle_get_subtree(self, args) -> int:
        """Handle --get-subtree operation."""
        task_id = getattr(args, "task_id", None)
        if not task_id:
            self.io.print_error("--task-id is required for --get-subtree")
            return 1

        try:
            from app.database import init_db
            from app.repository.tasks import SqliteTaskRepository

            init_db()
            repo = SqliteTaskRepository()
            subtree = repo.get_subtree(task_id)

            if not subtree:
                self.io.print_info(f"No subtree found for task {task_id}")
                return 0

            self.io.print_section(f"Subtree of Task {task_id}")

            def print_tree(tasks, level=0):
                for task in tasks:
                    indent = "  " * level
                    name = task.get("name", "No name")
                    status = task.get("status", "unknown")
                    print(f"{indent}• [{task['id']}] {name} ({status})")

                    children = [t for t in subtree if t.get("parent_id") == task["id"]]
                    if children:
                        print_tree(children, level + 1)

            # Find root tasks in subtree
            roots = [t for t in subtree if t.get("id") == task_id]
            print_tree(roots)

            return 0

        except Exception as e:
            self.io.print_error(f"Get subtree failed: {e}")
            return 1

    def _handle_move_task(self, args) -> int:
        """Handle --move-task operation."""
        task_id = getattr(args, "task_id", None)
        new_parent_id = getattr(args, "new_parent_id", None)

        if not task_id:
            self.io.print_error("--task-id is required for --move-task")
            return 1

        # new_parent_id can be None or -1 for root level
        if new_parent_id == -1:
            new_parent_id = None

        try:
            from app.database import init_db
            from app.repository.tasks import SqliteTaskRepository

            init_db()
            repo = SqliteTaskRepository()

            # Move the task
            repo.move_task(task_id, new_parent_id)

            parent_desc = "root level" if new_parent_id is None else f"under task {new_parent_id}"
            self.io.print_success(f"Task {task_id} moved to {parent_desc}")
            return 0

        except Exception as e:
            self.io.print_error(f"Move task failed: {e}")
            return 1

    def _handle_generate_embeddings(self, args) -> int:
        """Handle --generate-embeddings operation."""
        try:
            from app.services.embeddings import get_embeddings_service

            service = get_embeddings_service()
            batch_size = getattr(args, "embedding_batch_size", 10)

            self.io.print_info(f"Generating embeddings (batch size: {batch_size})...")
            result = service.precompute_embeddings_for_completed_tasks(batch_size=batch_size)

            self.io.print_success(f"Generated embeddings for {result} tasks")
            return 0

        except Exception as e:
            self.io.print_error(f"Generate embeddings failed: {e}")
            return 1

    def _handle_embedding_stats(self, args) -> int:
        """Handle --embedding-stats operation."""
        try:
            from app.services.embeddings import get_embeddings_service

            service = get_embeddings_service()
            info = service.get_service_info()

            self.io.print_section("Embedding Service Statistics")
            for key, value in info.items():
                print(f"  {key}: {value}")

            return 0

        except Exception as e:
            self.io.print_error(f"Embedding stats failed: {e}")
            return 1

    def _handle_rebuild_embeddings(self, args) -> int:
        """Handle --rebuild-embeddings operation."""
        if not self.io.confirm("This will rebuild ALL embeddings. Continue?", default=False):
            self.io.print_info("Operation cancelled")
            return 0

        try:
            from app.services.embeddings import get_embeddings_service

            service = get_embeddings_service()
            batch_size = getattr(args, "embedding_batch_size", 10)

            self.io.print_info(f"Rebuilding all embeddings (batch size: {batch_size})...")

            # This would need to be implemented in the embeddings service
            # For now, just show what we would do
            self.io.print_info("Rebuild embeddings not yet implemented - would clear cache and regenerate all")
            return 0

        except Exception as e:
            self.io.print_error(f"Rebuild embeddings failed: {e}")
            return 1

    def _handle_benchmark(self, args) -> int:
        """Handle --benchmark operation: run multi-config report generation & LLM scoring."""
        try:
            topic = getattr(args, "benchmark_topic", None)
            configs = getattr(args, "benchmark_configs", None)
            sections = getattr(args, "benchmark_sections", 5)
            output = getattr(args, "benchmark_output", None)

            if not topic:
                self.io.print_error("--benchmark-topic is required")
                return 1
            if not configs or not isinstance(configs, list):
                self.io.print_error("--benchmark-configs is required (one or more specs)")
                return 1

            # Lazy import to avoid heavy deps at startup
            try:
                from app.services.evaluation.benchmark import run_benchmark
            except Exception:
                from ..app.services.benchmark import (
                    run_benchmark,  # fallback when running as module
                )

            self.io.print_info("Running benchmark...")
            out = run_benchmark(topic, configs, sections=sections)
            summary = out.get("summary_md", "")

            if output:
                from .utils.file_utils import FileUtils

                if FileUtils.write_file_safe(output, summary):
                    self.io.print_success(f"Benchmark summary written to {output}")
                    return 0
                else:
                    self.io.print_error("Failed to write output file")
                    return 1
            else:
                print(summary)
                return 0
        except Exception as e:
            self.io.print_error(f"Benchmark failed: {e}")
            return 1

    def _show_help_guidance(self) -> int:
        """Show helpful guidance when no operation is specified."""
        self.io.print_section("GLM Agent CLI")
        self.io.print_info("This CLI helps you plan and execute projects with LLM assistance.")
        self.io.print_info("")
        self.io.print_info("Common operations:")
        self.io.print_info("  --goal 'Your goal here'           Create and execute a new plan")
        self.io.print_info("  --list-plans                      List all existing plans")
        self.io.print_info("  --execute-only --title 'Plan'     Execute an existing plan")
        self.io.print_info("  --rerun-task 123                  Rerun a specific task")
        self.io.print_info("  --rerun-interactive --title 'Plan' Interactive task rerun")
        self.io.print_info("")
        self.io.print_info("For full help: --help")
        return 0


def main():
    """Main entry point for the CLI application."""
    app = ModernCLIApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
