"""Plan processing utilities for CLI."""

import json
import re
from typing import Any, Dict, List, Optional

from .file_utils import FileUtils


class PlanUtils:
    """Utilities for plan processing and management."""
    
    PLAN_MD = "plan.md"
    PLAN_JSON = "plan.json"
    OUTPUT_MD = "output.md"
    
    @staticmethod
    def render_plan_md(plan: Dict[str, Any]) -> str:
        """Render a plan as Markdown with embedded JSON."""
        title = plan.get("title", "Untitled Plan")
        tasks = plan.get("tasks") or []
        
        lines = [
            f"# Plan: {title}",
            "",
            "This document describes the proposed plan. You can edit the JSON block below (title, tasks, priorities).",
            "",
            "- Edit the JSON in the code block, then save.",
            "- After saving, return to the terminal and press Enter to continue.",
            "",
            "```json plan",
            json.dumps(plan, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Tasks (preview)"
        ]
        
        for task in tasks:
            name = task.get("name", "")
            priority = task.get("priority", "")
            prompt = task.get("prompt", "")
            lines.append(f"- [{priority}] {name}: {prompt}")
        
        lines.append("")
        return "\\n".join(lines)
    
    @staticmethod
    def extract_plan_from_md(markdown_content: str) -> Optional[Dict[str, Any]]:
        """Extract plan JSON from Markdown content."""
        # Look for a fenced code block starting with ```json plan
        pattern = r"```json\\s*plan\\s*\\n(.*?)```"
        match = re.search(pattern, markdown_content, flags=re.S | re.I)
        
        if not match:
            return None
        
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            print(f"❌ JSON parse error: {e}")
            return None
    
    @staticmethod
    def ensure_priorities(plan: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure all tasks in the plan have priority values."""
        tasks = plan.get("tasks", [])
        
        for i, task in enumerate(tasks):
            if "priority" not in task or task["priority"] is None:
                task["priority"] = (i + 1) * 10
        
        plan["tasks"] = tasks
        return plan
    
    @staticmethod
    def save_plan_files(plan: Dict[str, Any]) -> bool:
        """Save plan to both JSON and Markdown files."""
        try:
            # Save JSON file
            json_content = json.dumps(plan, ensure_ascii=False, indent=2)
            if not FileUtils.write_file_safe(PlanUtils.PLAN_JSON, json_content):
                return False
            
            # Save Markdown file
            md_content = PlanUtils.render_plan_md(plan)
            if not FileUtils.write_file_safe(PlanUtils.PLAN_MD, md_content):
                return False
            
            return True
        except Exception as e:
            print(f"❌ Error saving plan files: {e}")
            return False
    
    @staticmethod
    def load_plan_from_file(file_path: str = None) -> Optional[Dict[str, Any]]:
        """Load plan from JSON or Markdown file."""
        if file_path is None:
            # Try JSON first, then Markdown
            for path in [PlanUtils.PLAN_JSON, PlanUtils.PLAN_MD]:
                result = PlanUtils.load_plan_from_file(path)
                if result:
                    return result
            return None
        
        content = FileUtils.read_file_safe(file_path)
        if not content:
            return None
        
        if file_path.endswith('.json'):
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                print(f"❌ JSON parse error in {file_path}: {e}")
                return None
        
        elif file_path.endswith('.md'):
            return PlanUtils.extract_plan_from_md(content)
        
        else:
            print(f"❌ Unsupported file format: {file_path}")
            return None
    
    @staticmethod
    def validate_plan(plan: Dict[str, Any]) -> bool:
        """Validate that a plan has required fields."""
        if not isinstance(plan, dict):
            print("❌ Plan must be a dictionary")
            return False
        
        if "title" not in plan:
            print("❌ Plan must have a 'title' field")
            return False
        
        if "tasks" not in plan:
            print("❌ Plan must have a 'tasks' field")
            return False
        
        tasks = plan["tasks"]
        if not isinstance(tasks, list):
            print("❌ Plan 'tasks' must be a list")
            return False
        
        for i, task in enumerate(tasks):
            if not isinstance(task, dict):
                print(f"❌ Task {i+1} must be a dictionary")
                return False
            
            required_fields = ["name", "prompt"]
            for field in required_fields:
                if field not in task:
                    print(f"❌ Task {i+1} missing required field: {field}")
                    return False
        
        return True