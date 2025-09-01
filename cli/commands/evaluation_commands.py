"""
CLI commands for evaluation system

Provides command-line interface for content evaluation and quality management.
"""

import os
import sys
from argparse import Namespace
from typing import Any, Dict, Optional

# Add app path for imports when running from tests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .base import MultiCommand
from ..utils.io_utils import IOUtils

# Corrected, direct imports
from app.repository.tasks import default_repo
from app.executor import execute_task_with_evaluation
from app.models import EvaluationConfig
# The following are not used in the current version but kept for potential future use
# from app.services.expert_evaluator import get_multi_expert_evaluator
# from app.services.evaluation_supervisor import get_evaluation_supervisor, get_supervision_report


class EvaluationCommands(MultiCommand):
    """CLI commands for evaluation system"""
    
    @property
    def name(self) -> str:
        return "evaluation"
    
    @property
    def description(self) -> str:
        return "Content evaluation and quality management commands"
    
    def get_action_map(self) -> Dict[str, callable]:
        """Map evaluation arguments to handler methods."""
        return {
            'eval_execute': self.handle_execute,
            'eval_history': self.handle_history,
        }
    
    def handle_default(self, args: Namespace) -> int:
        """Handle default evaluation behavior."""
        self.io.print_info("Available evaluation operations:")
        self.io.print_info("  --eval-execute <task-id>      Execute task with basic evaluation")
        self.io.print_info("  --eval-history <task-id>      View evaluation history")
        return 0
    
    def handle_execute(self, args: Namespace) -> int:
        """Handle task execution with evaluation"""
        task_id = args.eval_execute
        if not task_id:
            self.io.print_error("Task ID is required for execution")
            return 1
        
        try:
            task = default_repo.get_task_info(task_id)
            if not task:
                self.io.print_error(f"Task {task_id} not found")
                return 1
            
            self.io.print_info(f"Executing task {task_id}: {task['name']}")
            
            result = execute_task_with_evaluation(task=task, repo=default_repo)
            
            default_repo.update_task_status(task_id, result.status)
            self.io.print_success(f"Execution finished with status: {result.status}")
            return 0
            
        except Exception as e:
            self.io.print_error(f"Execution failed: {e}")
            return 1
    
    def handle_history(self, args: Namespace) -> int:
        """Handle evaluation history viewing"""
        task_id = args.eval_history
        if not task_id:
            self.io.print_error("Task ID is required for history")
            return 1
        
        try:
            history = default_repo.get_evaluation_history(task_id)
            if not history:
                self.io.print_warning(f"No evaluation history found for task {task_id}")
                return 0
            
            for item in history:
                self.io.print_info(f"Iteration {item['iteration']}: Score={item['overall_score']:.2f}, Needs Revision: {item['needs_revision']}")
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to get evaluation history: {e}")
            return 1
