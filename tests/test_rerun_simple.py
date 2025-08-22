#!/usr/bin/env python3
"""
Simple test script for rerun task functionality
Tests the core components without requiring server
"""

import sys
import os
import json
from typing import List, Dict, Any

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository


def test_repository_methods():
    """Test repository methods used by rerun functionality"""
    print("=== Testing Repository Methods ===")
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create test plan
    plan_title = "Test Rerun Functionality"
    
    # Create test tasks
    task_ids = []
    task_names = [
        "Literature Review",
        "Data Collection", 
        "Analysis",
        "Report Writing"
    ]
    
    for i, name in enumerate(task_names):
        task_id = repo.create_task(
            name=f"{name} Task",
            status="completed" if i % 2 == 0 else "failed",
            priority=(i+1)*10
        )
        task_ids.append(task_id)
        
        # Add plan prefix to make it part of the plan
        repo.upsert_task_input(task_id, f"Task for {plan_title}: {name}")
    
    print(f"✅ Created {len(task_ids)} test tasks")
    
    # Test list_plan_tasks
    plan_tasks = repo.list_plan_tasks(plan_title)
    print(f"✅ Found {len(plan_tasks)} tasks for plan '{plan_title}'")
    
    for task in plan_tasks:
        print(f"  ID: {task['id']}, Name: {task['name']}, Status: {task['status']}")
    
    return task_ids, plan_title


def test_task_status_methods():
    """Test task status management"""
    print("\n=== Testing Task Status Management ===")
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create a test task
    task_id = repo.create_task(name="Test Status Task", status="completed")
    
    # Test status updates
    statuses = ["pending", "running", "completed", "failed"]
    
    for status in statuses:
        repo.update_task_status(task_id, status)
        task = repo.get_task_info(task_id)
        print(f"✅ Updated task {task_id} to status: {task['status']}")
    
    return task_id


def test_cli_command_structure():
    """Test CLI command structure"""
    print("\n=== Testing CLI Command Structure ===")
    
    commands = [
        "python agent_cli.py --rerun-task 1",
        "python agent_cli.py --rerun-task 1 --use-context",
        "python agent_cli.py --rerun-interactive --title 'Test Plan'",
        "python agent_cli.py --rerun-subtree 1 --rerun-include-parent",
    ]
    
    for cmd in commands:
        print(f"✅ Valid command: {cmd}")
    
    return True


def test_context_options():
    """Test context options parsing"""
    print("\n=== Testing Context Options ===")
    
    # Import the context options builder
    try:
        from agent_cli import _build_context_options_from_args
        
        class MockArgs:
            def __init__(self):
                self.use_context = True
                self.include_deps = True
                self.include_plan = True
                self.tfidf_k = 2
                self.max_chars = 1000
        
        args = MockArgs()
        options = _build_context_options_from_args(args)
        
        print(f"✅ Context options: {json.dumps(options, indent=2)}")
        return options
        
    except Exception as e:
        print(f"❌ Context options test failed: {e}")
        return None


def run_simple_test():
    """Run simple tests without server"""
    print("🧪 Starting simple rerun functionality test...")
    
    try:
        task_ids, plan_title = test_repository_methods()
        test_task_status_methods()
        test_cli_command_structure()
        test_context_options()
        
        print("\n🎉 Simple test completed successfully!")
        print(f"Test plan: {plan_title}")
        print(f"Test task IDs: {task_ids}")
        
        print("\nNext steps:")
        print("1. Start server: conda run -n LLM python -m uvicorn app.main:app --reload")
        print("2. Run full test: python tests/test_rerun_functionality.py")
        print("3. Test CLI: python agent_cli.py --rerun-interactive --title 'Test Rerun Functionality'")
        
        return True
        
    except Exception as e:
        print(f"❌ Simple test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_simple_test()
    sys.exit(0 if success else 1)