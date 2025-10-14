"""
任务管理命令 - 对应API的task_routes

这个模块提供了完整的任务管理CLI功能，通过API调用实现所有任务操作。
包括任务CRUD、子任务管理、任务执行等功能。
"""

import os
import sys
from argparse import Namespace
from typing import List, Dict, Any

# Add app path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from ..utils.api_client import get_api_client, APIClientError
from .base import MultiCommand


class TaskCommands(MultiCommand):
    """任务管理命令类"""
    
    def __init__(self):
        super().__init__()
        self.api_client = get_api_client()
    
    @property
    def name(self) -> str:
        return "task"
    
    @property 
    def description(self) -> str:
        return "Task management operations (API-driven)"
    
    def get_action_map(self) -> Dict[str, callable]:
        """Map task arguments to handler methods."""
        return {
            "list_tasks": self.handle_list_tasks,
            "create_task": self.handle_create_task,
            "get_task": self.handle_get_task,
            "update_task": self.handle_update_task,
            "get_output": self.handle_get_output,
            "get_children": self.handle_get_children,
            "get_subtree": self.handle_get_subtree,
            "move_task": self.handle_move_task,
        }

    def handle_default(self, _args: Namespace) -> int:
        """Handle default task behavior."""
        self.io.print_info("Available task operations (API-driven):")
        self.io.print_info("  --list-tasks                   List all tasks")
        self.io.print_info("  --create-task <name>           Create a new task")
        self.io.print_info("  --get-task <id>                Get task details")
        self.io.print_info("  --update-task <id>             Update task status")
        self.io.print_info("  --get-output <id>              Get task output")
        self.io.print_info("  --get-children <id>            Get task children")
        self.io.print_info("  --get-subtree <id>             Get complete subtree")
        self.io.print_info("  --move-task <id>               Move task to new parent")
        return 0
    
    def handle_list_tasks(self, args: Namespace) -> int:
        """列出所有任务 - 对应 GET /tasks"""
        try:
            tasks = self.api_client.get("/tasks")
            
            self.io.print_section("All Tasks (via API)")
            if not tasks:
                self.io.print_warning("No tasks found")
                return 0
            
            # Display tasks in a formatted way
            self._display_task_list(tasks)
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to list tasks: {e}")
            return 1
    
    def handle_create_task(self, args: Namespace) -> int:
        """创建任务 - 对应 POST /tasks"""
        name = getattr(args, "create_task", None) or getattr(args, "task_name", None)
        task_type = getattr(args, "task_type", "atomic")
        
        if not name:
            self.io.print_error("Task name is required")
            return 1
        
        try:
            payload = {
                "name": name,
                "task_type": task_type
            }
            
            result = self.api_client.post("/tasks", json_data=payload)
            
            task_id = result.get('id')
            self.io.print_success("Task created successfully via API")
            self.io.print_info(f"  Task ID: {task_id}")
            self.io.print_info(f"  Name: {result.get('name')}")
            self.io.print_info(f"  Type: {result.get('task_type', 'atomic')}")
            self.io.print_info(f"  Status: {result.get('status')}")
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to create task: {e}")
            return 1
    
    def handle_get_task(self, args: Namespace) -> int:
        """获取单个任务 - 对应 GET /tasks/{id}"""
        task_id = getattr(args, "get_task", None) or getattr(args, "task_id", None)
        
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            task = self.api_client.get(f"/tasks/{task_id}")
            
            self.io.print_section(f"Task {task_id} Details (via API)")
            self.io.print_info(f"ID: {task.get('id')}")
            self.io.print_info(f"Name: {task.get('name')}")
            self.io.print_info(f"Status: {task.get('status')}")
            self.io.print_info(f"Type: {task.get('task_type', 'atomic')}")
            self.io.print_info(f"Priority: {task.get('priority', 'N/A')}")
            
            # Show parent info if available
            parent_id = task.get('parent_id')
            if parent_id:
                self.io.print_info(f"Parent ID: {parent_id}")
            
            # Show hierarchy info if available
            path = task.get('path')
            depth = task.get('depth')
            if path:
                self.io.print_info(f"Path: {path}")
            if depth is not None:
                self.io.print_info(f"Depth: {depth}")
            
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to get task: {e}")
            return 1
    
    def handle_update_task(self, args: Namespace) -> int:
        """更新任务 - 对应 PUT /tasks/{id}"""
        task_id = getattr(args, "update_task", None) or getattr(args, "task_id", None)
        status = getattr(args, "status", None)
        name = getattr(args, "name", None)
        
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            payload = {}
            if status:
                payload["status"] = status
            if name:
                payload["name"] = name
            
            if not payload:
                self.io.print_error("At least one field (status, name) is required for update")
                return 1
            
            result = self.api_client.put(f"/tasks/{task_id}", json_data=payload)
            
            self.io.print_success(f"Task {task_id} updated successfully via API")
            if result:
                self.io.print_info("Updated task:")
                self.io.print_info(f"  ID: {result.get('id')}")
                self.io.print_info(f"  Name: {result.get('name')}")
                self.io.print_info(f"  Status: {result.get('status')}")
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to update task: {e}")
            return 1
    
    def handle_get_output(self, args: Namespace) -> int:
        """获取任务输出 - 对应 GET /tasks/{id}/output"""
        task_id = getattr(args, "get_output", None) or getattr(args, "task_id", None)
        
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            output = self.api_client.get(f"/tasks/{task_id}/output")
            
            self.io.print_section(f"Task {task_id} Output (via API)")
            content = output.get('content', '')
            if content:
                print(content)
            else:
                self.io.print_warning("No content available for this task")
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to get task output: {e}")
            return 1

    def handle_get_children(self, args: Namespace) -> int:
        """获取任务子任务 - 对应 GET /tasks/{id}/children"""
        task_id = getattr(args, "get_children", None) or getattr(args, "task_id", None)
        
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            result = self.api_client.get(f"/tasks/{task_id}/children")
            
            children = result.get('children', [])
            self.io.print_section(f"Children of Task {task_id} (via API)")
            
            if not children:
                self.io.print_info("No children found for this task")
                return 0
            
            self._display_task_list(children)
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to get task children: {e}")
            return 1

    def handle_get_subtree(self, args: Namespace) -> int:
        """获取任务子树 - 对应 GET /tasks/{id}/subtree"""
        task_id = getattr(args, "get_subtree", None) or getattr(args, "task_id", None)
        
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        try:
            result = self.api_client.get(f"/tasks/{task_id}/subtree")
            
            subtree = result.get('subtree', [])
            self.io.print_section(f"Subtree of Task {task_id} (via API)")
            
            if not subtree:
                self.io.print_info("No subtree found for this task")
                return 0
            
            # Display subtree in hierarchical format
            self._display_task_tree(subtree, task_id)
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to get task subtree: {e}")
            return 1

    def handle_move_task(self, args: Namespace) -> int:
        """移动任务 - 对应 POST /tasks/{id}/move"""
        task_id = getattr(args, "move_task", None) or getattr(args, "task_id", None)
        new_parent_id = getattr(args, "new_parent_id", None)
        
        if not task_id:
            self.io.print_error("Task ID is required")
            return 1
        
        # new_parent_id can be None for root level
        if new_parent_id == -1:
            new_parent_id = None
        
        try:
            payload = {"new_parent_id": new_parent_id}
            result = self.api_client.post(f"/tasks/{task_id}/move", json_data=payload)
            
            if result.get("ok"):
                parent_desc = "root level" if new_parent_id is None else f"under task {new_parent_id}"
                self.io.print_success(f"Task {task_id} moved to {parent_desc} via API")
            else:
                self.io.print_error("Move operation failed")
                return 1
            
            return 0
            
        except APIClientError as e:
            self.io.print_error(f"Failed to move task: {e}")
            return 1

    # Helper methods for display
    def _display_task_list(self, tasks: List[Dict[str, Any]]):
        """Display a list of tasks in a formatted way"""
        if not tasks:
            return
        
        self.io.print_info(f"Found {len(tasks)} tasks:")
        for task in tasks:
            task_id = task.get('id')
            name = task.get('name', 'No name')
            status = task.get('status', 'unknown')
            task_type = task.get('task_type', 'atomic')
            priority = task.get('priority', 'N/A')
            
            self.io.print_info(f"  [{task_id}] {name}")
            self.io.print_info(f"      Status: {status}, Type: {task_type}, Priority: {priority}")

    def _display_task_tree(self, subtree: List[Dict[str, Any]], root_task_id: int):
        """Display task subtree in hierarchical format"""
        if not subtree:
            return
        
        # Build a map for easy lookup
        task_map = {task['id']: task for task in subtree}
        
        def print_tree(task_id: int, level: int = 0):
            if task_id not in task_map:
                return
            
            task = task_map[task_id]
            indent = "  " * level
            name = task.get('name', 'No name')
            status = task.get('status', 'unknown')
            task_type = task.get('task_type', 'atomic')
            
            self.io.print_info(f"{indent}• [{task_id}] {name} ({status}, {task_type})")
            
            # Find and print children
            children = [t for t in subtree if t.get('parent_id') == task_id]
            children.sort(key=lambda x: (x.get('priority', 100), x.get('id', 0)))
            for child in children:
                print_tree(child['id'], level + 1)
        
        # Start from root
        print_tree(root_task_id)


def register_task_commands():
    """Register task commands with CLI"""
    return TaskCommands()
