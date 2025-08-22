"""Base command class with common functionality."""

import sys
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, Optional

from ..interfaces import CLICommand
from ..utils import IOUtils


class BaseCommand(CLICommand):
    """Base class for all CLI commands with common functionality."""
    
    def __init__(self):
        self.io = IOUtils()
    
    def add_arguments(self, parser: ArgumentParser) -> None:
        """Default implementation - commands can override if needed."""
        pass
    
    def execute(self, args: Namespace) -> int:
        """Execute the command with error handling."""
        try:
            return self._execute_impl(args)
        except KeyboardInterrupt:
            self.io.print_warning("Operation cancelled by user")
            return 1
        except Exception as e:
            self.io.print_error(f"Command failed: {e}")
            return 1
    
    def _execute_impl(self, args: Namespace) -> int:
        """Implementation method to be overridden by subclasses."""
        raise NotImplementedError("Subclasses must implement _execute_impl")
    
    def validate_required_args(self, args: Namespace, required: List[str]) -> bool:
        """Validate that required arguments are present."""
        missing = []
        for arg in required:
            if not hasattr(args, arg) or getattr(args, arg) is None:
                missing.append(arg)
        
        if missing:
            self.io.print_error(f"Missing required arguments: {', '.join(missing)}")
            return False
        
        return True
    
    def confirm_action(self, message: str, default: bool = False) -> bool:
        """Ask for user confirmation."""
        return self.io.confirm(message, default)
    
    def handle_api_error(self, response: dict, operation: str) -> bool:
        """Handle API response errors uniformly."""
        if not response.get('success', True):
            error = response.get('error', 'Unknown error')
            self.io.print_error(f"{operation} failed: {error}")
            return False
        return True
    
    def print_task_summary(self, task: dict) -> None:
        """Print a formatted task summary."""
        task_id = task.get('id', 'N/A')
        name = task.get('name', 'No name')
        status = task.get('status', 'unknown')
        priority = task.get('priority', 'N/A')
        
        print(f"Task [{task_id}]: {name}")
        print(f"  Status: {status}")
        print(f"  Priority: {priority}")
        
        if 'created_at' in task:
            print(f"  Created: {task['created_at']}")
    
    def print_plan_summary(self, plan: dict) -> None:
        """Print a formatted plan summary."""
        title = plan.get('title', 'Untitled')
        tasks = plan.get('tasks', [])
        
        print(f"Plan: {title}")
        print(f"Tasks: {len(tasks)}")
        
        for i, task in enumerate(tasks[:5], 1):  # Show first 5 tasks
            name = task.get('name', 'No name')
            priority = task.get('priority', 'N/A')
            print(f"  {i}. [{priority}] {name}")
        
        if len(tasks) > 5:
            print(f"  ... and {len(tasks) - 5} more tasks")


class MultiCommand(BaseCommand):
    """Base class for commands that handle multiple operations."""
    
    def _execute_impl(self, args: Namespace) -> int:
        """Route to specific handler based on arguments."""
        # Try to find the first action that is set
        action_map = self.get_action_map()
        
        for action, handler in action_map.items():
            if hasattr(args, action) and getattr(args, action):
                return handler(args)
        
        # If no specific action, run default behavior
        return self.handle_default(args)
    
    def get_action_map(self) -> Dict[str, callable]:
        """Return mapping of argument names to handler methods."""
        raise NotImplementedError("Subclasses must implement get_action_map")
    
    def handle_default(self, args: Namespace) -> int:
        """Handle default behavior when no specific action is set."""
        self.io.print_error("No action specified")
        return 1