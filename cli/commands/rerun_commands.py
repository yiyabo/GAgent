"""Task rerun command implementations."""

from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, Optional

import requests

# Import app modules
from app.database import init_db
from app.repository.tasks import SqliteTaskRepository

from ..parser import DefaultContextOptionsBuilder
from ..utils import IOUtils
from .base import MultiCommand


class RerunCommands(MultiCommand):
    """Handle all task rerun operations."""

    @property
    def name(self) -> str:
        return "rerun"

    @property
    def description(self) -> str:
        return "Rerun tasks with various strategies"

    def get_action_map(self) -> Dict[str, callable]:
        """Map rerun arguments to handler methods."""
        return {
            "rerun_task": self.handle_single_task,
            "rerun_subtree": self.handle_subtree,
            "rerun_interactive": self.handle_interactive,
        }

    def handle_default(self, args: Namespace) -> int:
        """Show rerun help when no specific action."""
        self.io.print_info("Available rerun operations:")
        self.io.print_info("  --rerun-task <id>     Rerun a specific task")
        self.io.print_info("  --rerun-subtree <id>  Rerun task and subtasks")
        self.io.print_info("  --rerun-interactive   Interactive task selection")
        return 0

    def handle_single_task(self, args: Namespace) -> int:
        """Handle single task rerun."""
        task_id = args.rerun_task

        if not self.validate_required_args(args, ["rerun_task"]):
            return 1

        self.io.print_section(f"Rerunning Task {task_id}")

        # Build context options
        context_builder = DefaultContextOptionsBuilder()
        context_options = context_builder.build_from_args(args)

        return self._rerun_single_task(task_id, args.use_context, context_options)

    def handle_subtree(self, args: Namespace) -> int:
        """Handle subtree rerun."""
        task_id = args.rerun_subtree

        if not self.validate_required_args(args, ["rerun_subtree"]):
            return 1

        self.io.print_section(f"Rerunning Subtree from Task {task_id}")

        # Build context options
        context_builder = DefaultContextOptionsBuilder()
        context_options = context_builder.build_from_args(args)

        include_parent = getattr(args, "rerun_include_parent", False)

        return self._rerun_subtree(task_id, args.use_context, include_parent, context_options)

    def handle_interactive(self, args: Namespace) -> int:
        """Handle interactive task selection for rerun."""
        if not args.title:
            self.io.print_error("Interactive rerun requires --title argument")
            return 1

        self.io.print_section(f"Interactive Rerun for Plan: {args.title}")

        # Initialize database and repository
        init_db()
        repo = SqliteTaskRepository()

        # Build context options
        context_builder = DefaultContextOptionsBuilder()
        context_options = context_builder.build_from_args(args)

        return self._interactive_rerun_tasks(repo, args.title, args.use_context, context_options)

    def _rerun_single_task(
        self, task_id: int, use_context: bool = False, context_options: Optional[Dict[str, Any]] = None
    ) -> int:
        """Rerun a single task via API."""
        try:
            payload = {"use_context": use_context}
            if context_options:
                payload["context_options"] = context_options

            response = requests.post(f"http://127.0.0.1:8000/tasks/{task_id}/rerun", json=payload, timeout=300)

            if response.status_code == 200:
                result = response.json()
                self.io.print_success(f"Task {task_id} rerun completed")

                if "output" in result:
                    print("\\nTask output:")
                    print(result["output"])

                return 0
            else:
                self.io.print_error(f"Rerun failed: HTTP {response.status_code}")
                if response.text:
                    print(f"Error: {response.text}")
                return 1

        except requests.exceptions.ConnectionError:
            self.io.print_error("Cannot connect to server. Is it running on port 8000?")
            return 1
        except Exception as e:
            self.io.print_error(f"Rerun failed: {e}")
            return 1

    def _rerun_subtree(
        self,
        task_id: int,
        use_context: bool = False,
        include_parent: bool = True,
        context_options: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Rerun task subtree via API."""
        try:
            payload = {"use_context": use_context, "include_parent": include_parent}
            if context_options:
                payload["context_options"] = context_options

            response = requests.post(
                f"http://127.0.0.1:8000/tasks/{task_id}/rerun-subtree",
                json=payload,
                timeout=600,  # Longer timeout for subtree operations
            )

            if response.status_code == 200:
                result = response.json()
                rerun_count = result.get("tasks_rerun", 0)
                self.io.print_success(f"Subtree rerun completed: {rerun_count} tasks")

                if "task_ids" in result:
                    self.io.print_info(f"Rerun task IDs: {result['task_ids']}")

                return 0
            else:
                self.io.print_error(f"Subtree rerun failed: HTTP {response.status_code}")
                if response.text:
                    print(f"Error: {response.text}")
                return 1

        except requests.exceptions.ConnectionError:
            self.io.print_error("Cannot connect to server. Is it running on port 8000?")
            return 1
        except Exception as e:
            self.io.print_error(f"Subtree rerun failed: {e}")
            return 1

    def _interactive_rerun_tasks(
        self,
        repo: SqliteTaskRepository,
        plan_title: str,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Interactive task selection and rerun."""
        try:
            # Get tasks for the plan
            tasks = self._get_plan_tasks(repo, plan_title)
            if not tasks:
                self.io.print_error(f"No tasks found for plan: {plan_title}")
                return 1

            # Let user select tasks
            selected_tasks = self._select_tasks_for_rerun(tasks)
            if not selected_tasks:
                self.io.print_info("No tasks selected for rerun")
                return 0

            # Confirm the operation
            task_names = [t["name"] for t in selected_tasks]
            if not self.confirm_action(
                f"Rerun {len(selected_tasks)} tasks: {', '.join(task_names[:3])}{'...' if len(task_names) > 3 else ''}?"
            ):
                self.io.print_info("Operation cancelled")
                return 0

            # Rerun selected tasks
            success_count = 0
            for task in selected_tasks:
                task_id = task["id"]
                self.io.print_info(f"Rerunning task {task_id}: {task['name']}")

                if self._rerun_single_task(task_id, use_context, context_options) == 0:
                    success_count += 1
                else:
                    self.io.print_warning(f"Failed to rerun task {task_id}")

            self.io.print_success(f"Successfully rerun {success_count}/{len(selected_tasks)} tasks")
            return 0 if success_count > 0 else 1

        except Exception as e:
            self.io.print_error(f"Interactive rerun failed: {e}")
            return 1

    def _get_plan_tasks(self, repo: SqliteTaskRepository, plan_title: str) -> List[dict]:
        """Get all tasks for a specific plan."""
        try:
            return repo.list_plan_tasks(plan_title)
        except Exception as e:
            self.io.print_error(f"Failed to get plan tasks: {e}")
            return []

    def _select_tasks_for_rerun(self, tasks: List[dict]) -> List[dict]:
        """Interactive task selection."""
        if not tasks:
            return []

        # Filter to completed or failed tasks (rerunnable)
        rerunnable_tasks = [t for t in tasks if t.get("status") in ["completed", "failed"]]

        if not rerunnable_tasks:
            self.io.print_warning("No rerunnable tasks found (only completed/failed tasks can be rerun)")
            return []

        self.io.print_info(f"Found {len(rerunnable_tasks)} rerunnable tasks:")
        self.io.print_task_list(rerunnable_tasks)

        # Simple selection - ask for task IDs
        selected_ids_str = self.io.safe_input("\\nEnter task IDs to rerun (comma-separated, or 'all' for all): ")

        if not selected_ids_str:
            return []

        if selected_ids_str.lower() == "all":
            return rerunnable_tasks

        try:
            selected_ids = [int(x.strip()) for x in selected_ids_str.split(",")]
            return [t for t in rerunnable_tasks if t["id"] in selected_ids]
        except ValueError:
            self.io.print_error("Invalid task ID format")
            return []
