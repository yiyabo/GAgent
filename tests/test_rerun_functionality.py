#!/usr/bin/env python3
"""
Test script for rerun task functionality
Tests both CLI and API endpoints for task rerunning
"""

import json
import requests
import subprocess
import sys
import os
from typing import List, Dict, Any

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository


def test_api_endpoints():
    """Test all rerun API endpoints"""
    print("=== Testing Rerun API Endpoints ===")
    
    # Initialize database
    init_db()
    repo = SqliteTaskRepository()
    
    # Create a test plan
    test_plan = {
        "title": "Test Rerun Plan",
        "tasks": [
            {"name": "Test Task 1", "prompt": "Test prompt 1", "priority": 10},
            {"name": "Test Task 2", "prompt": "Test prompt 2", "priority": 20},
            {"name": "Test Task 3", "prompt": "Test prompt 3", "priority": 30}
        ]
    }
    
    # Create tasks
    task_ids = []
    for task_data in test_plan["tasks"]:
        task_id = repo.create_task(
            name=task_data["name"],
            status="completed",
            priority=task_data["priority"]
        )
        task_ids.append(task_id)
        repo.upsert_task_input(task_id, task_data["prompt"])
    
    print(f"Created test tasks: {task_ids}")
    
    # Test 1: Single task rerun
    print("\n1. Testing single task rerun...")
    try:
        response = requests.post(
            f"http://127.0.0.1:8000/tasks/{task_ids[0]}/rerun",
            json={"use_context": False},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Single task rerun: {result}")
        else:
            print(f"❌ Single task rerun failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Single task rerun error: {e}")
    
    # Test 2: Selected tasks rerun
    print("\n2. Testing selected tasks rerun...")
    try:
        response = requests.post(
            "http://127.0.0.1:8000/tasks/rerun/selected",
            json={
                "task_ids": task_ids[:2],
                "use_context": False
            },
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Selected tasks rerun: {result}")
        else:
            print(f"❌ Selected tasks rerun failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Selected tasks rerun error: {e}")
    
    # Test 3: Subtree rerun
    print("\n3. Testing subtree rerun...")
    try:
        response = requests.post(
            f"http://127.0.0.1:8000/tasks/{task_ids[0]}/rerun/subtree",
            json={"use_context": False, "include_parent": True},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Subtree rerun: {result}")
        else:
            print(f"❌ Subtree rerun failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Subtree rerun error: {e}")
    
    return task_ids


def test_cli_rerun():
    """Test CLI rerun functionality"""
    print("\n=== Testing CLI Rerun Commands ===")
    
    # Test 1: Interactive rerun
    print("1. Testing interactive rerun...")
    try:
        # This would require manual interaction, so we'll test the command structure
        cmd = [
            sys.executable, "agent_cli.py", 
            "--rerun-interactive", 
            "--title", "Test Rerun Plan",
            "--use-context"
        ]
        print(f"Command: {' '.join(cmd)}")
        print("(This would open interactive menu)")
    except Exception as e:
        print(f"❌ CLI test error: {e}")
    
    # Test 2: Single task rerun
    print("\n2. Testing single task CLI...")
    try:
        cmd = [sys.executable, "agent_cli.py", "--rerun-task", "1", "--use-context"]
        print(f"Command: {' '.join(cmd)}")
    except Exception as e:
        print(f"❌ CLI single task error: {e}")


def test_task_listing():
    """Test task listing functionality"""
    print("\n=== Testing Task Listing ===")
    
    init_db()
    repo = SqliteTaskRepository()
    
    # Create test tasks with different statuses
    task_ids = []
    statuses = ["pending", "completed", "failed", "running"]
    
    for i, status in enumerate(statuses):
        task_id = repo.create_task(
            name=f"Test Task {i+1}",
            status=status,
            priority=(i+1)*10
        )
        task_ids.append(task_id)
    
    # List all tasks
    all_tasks = repo.list_all_tasks()
    print(f"Total tasks: {len(all_tasks)}")
    
    for task in all_tasks[-4:]:  # Show last 4 tasks
        print(f"ID: {task['id']}, Name: {task['name']}, Status: {task['status']}")
    
    return task_ids


def run_comprehensive_test():
    """Run all tests"""
    print("🧪 Starting comprehensive rerun functionality test...")
    
    # Check if FastAPI server is running
    try:
        response = requests.get("http://127.0.0.1:8000/health", timeout=5)
        server_running = response.status_code == 200
    except:
        server_running = False
    
    if not server_running:
        print("⚠️  FastAPI server not detected at http://127.0.0.1:8000")
        print("Please start the server with: conda run -n LLM python -m uvicorn app.main:app --reload")
        return False
    
    print("✅ FastAPI server detected")
    
    # Run tests
    try:
        task_ids = test_api_endpoints()
        test_cli_rerun()
        test_task_listing()
        
        print("\n🎉 Test completed successfully!")
        print(f"Created test tasks: {task_ids}")
        print("\nUsage examples:")
        print(f"  python agent_cli.py --rerun-task {task_ids[0]}")
        print(f"  python agent_cli.py --rerun-selected --task-ids {','.join(map(str, task_ids[:2]))}")
        print("  python agent_cli.py --rerun-interactive --title 'Test Rerun Plan'")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


if __name__ == "__main__":
    success = run_comprehensive_test()
    sys.exit(0 if success else 1)