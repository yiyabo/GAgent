"""Task management command implementations."""

import sys
import os
from argparse import Namespace
from typing import Dict, Any

# Add app path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from .base import MultiCommand
from ..utils.io_utils import IOUtils

try:
    from ...app.repository.tasks import default_repo
except ImportError:
    from app.repository.tasks import default_repo


class TaskCommands(MultiCommand):
    """Handle task management operations including editing."""
    
    def __init__(self):
        super().__init__()
        self.io = IOUtils()
    
    @property
    def name(self) -> str:
        return "task"
    
    @property
    def description(self) -> str:
        return "Task management operations including editing"
    
    def get_action_map(self) -> Dict[str, Any]:
        return {
            'edit_task': self.handle_edit_task,
            'edit_input': self.handle_edit_input,
            'get_input': self.handle_get_input,
        }
    
    def handle_default(self, args: Namespace) -> int:
        """Show available task management commands."""
        self.io.print_info("Available task management commands:")
        self.io.print_info("  --edit-task <task-id> [--name <name>] [--status <status>] [--priority <priority>] [--type <type>]")
        self.io.print_info("  --edit-input <task-id> --prompt <prompt>")
        self.io.print_info("  --get-input <task-id>")
        return 0
    
    def handle_edit_task(self, args: Namespace) -> int:
        """Edit task properties."""
        task_id = getattr(args, 'edit_task', None)
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            task_id = int(task_id)
        except ValueError:
            self.io.print_error("Invalid task ID")
            return 1
        
        # Check if task exists
        task = default_repo.get_task(task_id)
        if not task:
            self.io.print_error(f"Task {task_id} not found")
            return 1
        
        # Build update parameters
        updates = {}
        if hasattr(args, 'name') and args.name:
            updates['name'] = args.name
        if hasattr(args, 'status') and args.status:
            updates['status'] = args.status
        if hasattr(args, 'priority') and args.priority is not None:
            try:
                updates['priority'] = int(args.priority)
            except ValueError:
                self.io.print_error("Priority must be an integer")
                return 1
        if hasattr(args, 'type') and args.type:
            updates['task_type'] = args.type
        
        if not updates:
            self.io.print_error("No fields to update provided")
            return 1
        
        # Update task
        updated = default_repo.update_task(task_id, **updates)
        if updated:
            self.io.print_success(f"Task {task_id} updated successfully")
            updated_task = default_repo.get_task(task_id)
            self.io.print_info(f"Updated task: {updated_task}")
            return 0
        else:
            self.io.print_error(f"Failed to update task {task_id}")
            return 1
    
    def handle_edit_input(self, args: Namespace) -> int:
        """Edit task input prompt."""
        task_id = getattr(args, 'edit_input', None)
        prompt = getattr(args, 'prompt', None)
        
        if not task_id or not prompt:
            self.io.print_error("Both task ID and prompt are required")
            return 1
        
        try:
            task_id = int(task_id)
        except ValueError:
            self.io.print_error("Invalid task ID")
            return 1
        
        # Check if task exists
        task = default_repo.get_task(task_id)
        if not task:
            self.io.print_error(f"Task {task_id} not found")
            return 1
        
        # Update task input
        default_repo.upsert_task_input(task_id, prompt)
        self.io.print_success(f"Task {task_id} input updated successfully")
        return 0
    
    def handle_get_input(self, args: Namespace) -> int:
        """Get task input prompt."""
        task_id = getattr(args, 'get_input', None)
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            task_id = int(task_id)
        except ValueError:
            self.io.print_error("Invalid task ID")
            return 1
        
        prompt = default_repo.get_task_input(task_id)
        if prompt is None:
            self.io.print_error(f"Task {task_id} input not found")
            return 1
        
        self.io.print_info(f"Task {task_id} input:")
        self.io.print_info(prompt)
        return 0