"""Plan management command implementations."""

import json
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, Optional

from .base import MultiCommand
from ..parser_v2 import LegacyCompatibilityWrapper
from ..utils import IOUtils, FileUtils, PlanUtils

# Import app modules
from app.database import init_db
from app.main import propose_plan, approve_plan, run_tasks, get_plan_assembled
from app.repository.tasks import SqliteTaskRepository


class PlanCommands(MultiCommand):
    """Handle plan management operations."""
    
    @property
    def name(self) -> str:
        return "plan"
    
    @property
    def description(self) -> str:
        return "Plan management operations"
    
    def get_action_map(self) -> Dict[str, callable]:
        """Map plan arguments to handler methods."""
        return {
            'list_plans': self.handle_list_plans,
            'load_plan': self.handle_load_plan,
            'execute_only': self.handle_execute_only,
            'plan_only': self.handle_plan_only,
        }
    
    def handle_default(self, args: Namespace) -> int:
        """Handle default plan creation workflow."""
        if not hasattr(args, 'goal') or not args.goal:
            self.io.print_error("Goal is required for plan creation")
            self.io.print_info("Use: --goal 'Your project goal here'")
            self.io.print_info("Or: --list-plans to see existing plans")
            self.io.print_info("Or: --execute-only --title 'Plan Name' to execute existing plan")
            return 1
        
        return self._create_and_execute_plan(args)
    
    def handle_list_plans(self, args: Namespace) -> int:
        """List all existing plans."""
        self.io.print_section("Existing Plans")
        
        try:
            init_db()
            repo = SqliteTaskRepository()
            
            # Get all plans by looking for distinct plan prefixes
            all_tasks = repo.list_all_tasks()
            plan_titles = set()
            
            for task in all_tasks:
                name = task.get('name', '')
                if '[' in name and ']' in name:
                    # Extract plan title from [Title] prefix
                    end = name.find(']')
                    if end > 0:
                        title = name[1:end]
                        plan_titles.add(title)
            
            if not plan_titles:
                self.io.print_warning("No plans found")
                return 0
            
            for i, title in enumerate(sorted(plan_titles), 1):
                print(f"  {i}. {title}")
                
                # Show task count for this plan
                plan_tasks = repo.list_plan_tasks(title)
                task_count = len(plan_tasks)
                completed_count = sum(1 for t in plan_tasks if t.get('status') == 'completed')
                print(f"     ({completed_count}/{task_count} tasks completed)")
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to list plans: {e}")
            return 1
    
    def handle_load_plan(self, args: Namespace) -> int:
        """Load and optionally rerun tasks from an existing plan."""
        plan_title = args.load_plan
        
        if not plan_title:
            self.io.print_error("Plan title is required")
            return 1
        
        self.io.print_section(f"Loading Plan: {plan_title}")
        
        try:
            init_db()
            repo = SqliteTaskRepository()
            
            # Get tasks for this plan
            tasks = repo.list_plan_tasks(plan_title)
            if not tasks:
                self.io.print_error(f"No tasks found for plan: {plan_title}")
                return 1
            
            self.io.print_success(f"Found {len(tasks)} tasks in plan")
            self.io.print_task_list(tasks)
            
            # Check if user wants to rerun tasks
            if self.confirm_action("Would you like to rerun some tasks?"):
                # Import rerun functionality
                from .rerun_commands import RerunCommands
                rerun_cmd = RerunCommands()
                
                # Set up args for interactive rerun
                rerun_args = Namespace()
                rerun_args.title = plan_title
                rerun_args.use_context = getattr(args, 'use_context', False)
                rerun_args.rerun_interactive = True
                
                # Copy context options
                for attr in ['semantic_k', 'min_similarity', 'max_chars', 'save_snapshot', 'label']:
                    if hasattr(args, attr):
                        setattr(rerun_args, attr, getattr(args, attr))
                
                return rerun_cmd.handle_interactive(rerun_args)
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to load plan: {e}")
            return 1
    
    def handle_execute_only(self, args: Namespace) -> int:
        """Execute an existing plan without creating new one."""
        title = getattr(args, 'title', None)
        
        if not title:
            self.io.print_error("Plan title is required for execution")
            self.io.print_info("Use: --execute-only --title 'Your Plan Name'")
            return 1
        
        self.io.print_section(f"Executing Plan: {title}")
        
        try:
            # Build context options using compatibility wrapper
            context_builder = LegacyCompatibilityWrapper()
            context_options = context_builder.build_from_args(args)
            
            # Execute the plan
            schedule = getattr(args, 'schedule', 'bfs')
            
            # Prepare payload for API call
            payload = {
                "title": title,
                "schedule": schedule,
                "use_context": args.use_context if hasattr(args, 'use_context') else False
            }
            if context_options:
                payload["context_options"] = context_options
            
            result = run_tasks(payload)
            
            # run_tasks returns a list of task results, not a dict with success/error
            if isinstance(result, list):
                completed_tasks = sum(1 for r in result if r.get('status') in ['completed', 'done'])
                total_tasks = len(result)
                
                self.io.print_success(f"Plan execution completed: {completed_tasks}/{total_tasks} tasks")
                
                # Optionally save assembled output
                output_path = getattr(args, 'output', 'output.md')
                if self.confirm_action(f"Save assembled output to {output_path}?"):
                    self._save_assembled_output(title, output_path)
                
                return 0
            else:
                # Handle error case (if result is a dict with error info)
                error = result.get('error', 'Unknown error') if isinstance(result, dict) else str(result)
                self.io.print_error(f"Plan execution failed: {error}")
                return 1
                
        except Exception as e:
            self.io.print_error(f"Execution failed: {e}")
            return 1
    
    def handle_plan_only(self, args: Namespace) -> int:
        """Create plan files without executing."""
        if not hasattr(args, 'goal') or not args.goal:
            self.io.print_error("Goal is required for plan creation")
            return 1
        
        self.io.print_section("Creating Plan (Plan-Only Mode)")
        
        try:
            # Generate plan
            plan_result = self._generate_plan(args)
            if not plan_result:
                return 1
            
            # Save plan files
            if PlanUtils.save_plan_files(plan_result):
                self.io.print_success(f"Plan saved to {PlanUtils.PLAN_JSON} and {PlanUtils.PLAN_MD}")
                self.io.print_info("Review and edit the plan, then run without --plan-only to execute")
                return 0
            else:
                self.io.print_error("Failed to save plan files")
                return 1
                
        except Exception as e:
            self.io.print_error(f"Plan creation failed: {e}")
            return 1
    
    def _create_and_execute_plan(self, args: Namespace) -> int:
        """Full workflow: create plan, review, and execute."""
        self.io.print_section("Creating and Executing Plan")
        
        try:
            # 1. Generate plan
            plan_result = self._generate_plan(args)
            if not plan_result:
                return 1
            
            # 2. Save and optionally review plan
            if not PlanUtils.save_plan_files(plan_result):
                self.io.print_error("Failed to save plan files")
                return 1
            
            self.io.print_success(f"Plan created with {len(plan_result.get('tasks', []))} tasks")
            
            # 3. Allow user to review/edit
            if not getattr(args, 'yes', False):  # Not auto-approve
                if not getattr(args, 'no_open', False):  # Not no-open
                    if self.confirm_action(f"Open {PlanUtils.PLAN_MD} for review/editing?"):
                        FileUtils.open_in_editor(PlanUtils.PLAN_MD)
                        self.io.safe_input("Press Enter after reviewing/editing the plan...")
                        
                        # Reload plan in case it was edited
                        edited_plan = PlanUtils.load_plan_from_file()
                        if edited_plan and PlanUtils.validate_plan(edited_plan):
                            plan_result = edited_plan
                        else:
                            self.io.print_warning("Using original plan (edited version invalid)")
            
            # 4. Approve plan (persist to database)
            approve_result = approve_plan(plan_result)
            if not self.handle_api_error(approve_result, "Plan approval"):
                return 1
            
            title = plan_result.get('title', 'Untitled')
            self.io.print_success(f"Plan approved and saved to database: {title}")
            
            # 5. Execute plan (if not plan-only)
            if not getattr(args, 'plan_only', False):
                if getattr(args, 'yes', False) or self.confirm_action("Execute the plan now?"):
                    # Execute using the same logic as execute_only
                    exec_args = Namespace()
                    exec_args.title = title
                    exec_args.use_context = getattr(args, 'use_context', False)
                    exec_args.schedule = getattr(args, 'schedule', 'bfs')
                    exec_args.output = getattr(args, 'output', 'output.md')
                    
                    # Copy context options
                    for attr in ['semantic_k', 'min_similarity', 'max_chars', 'per_section_max', 'strategy', 'save_snapshot', 'label']:
                        if hasattr(args, attr):
                            setattr(exec_args, attr, getattr(args, attr))
                    
                    return self.handle_execute_only(exec_args)
                else:
                    self.io.print_info(f"Plan saved. Execute later with: --execute-only --title '{title}'")
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Plan workflow failed: {e}")
            return 1
    
    def _generate_plan(self, args: Namespace) -> Optional[Dict[str, Any]]:
        """Generate a plan from the given goal."""
        payload = {
            'goal': args.goal,
        }
        
        # Add optional parameters
        if hasattr(args, 'title') and args.title:
            payload['title'] = args.title
        if hasattr(args, 'sections') and args.sections:
            payload['sections'] = args.sections
        if hasattr(args, 'style') and args.style:
            payload['style'] = args.style
        if hasattr(args, 'notes') and args.notes:
            payload['notes'] = args.notes
        
        self.io.print_info(f"Generating plan for goal: {args.goal}")
        
        try:
            result = propose_plan(payload)
            
            if not self.handle_api_error(result, "Plan generation"):
                return None
            
            # The propose_plan function returns the plan directly, not wrapped in a 'plan' field
            plan = result if isinstance(result, dict) and 'title' in result else result.get('plan')
            if not plan or not PlanUtils.validate_plan(plan):
                self.io.print_error("Generated plan is invalid")
                return None
            
            # Ensure priorities are set
            plan = PlanUtils.ensure_priorities(plan)
            
            return plan
            
        except Exception as e:
            self.io.print_error(f"Plan generation failed: {e}")
            return None
    
    def _save_assembled_output(self, title: str, output_path: str) -> bool:
        """Save assembled plan output to file."""
        try:
            result = get_plan_assembled(title)
            
            # get_plan_assembled returns a dict with 'title', 'sections', and 'combined' fields
            if not isinstance(result, dict) or 'combined' not in result:
                self.io.print_error("Failed to get assembled output")
                return False
            
            content = result.get('combined', '')
            if not content:
                self.io.print_error("No content found in assembled output")
                return False
                
            if FileUtils.write_file_safe(output_path, content):
                self.io.print_success(f"Assembled output saved to {output_path}")
                return True
            else:
                return False
                
        except Exception as e:
            self.io.print_error(f"Failed to save assembled output: {e}")
            return False