#!/usr/bin/env python
"""
Test script for DRY and async refactoring
Tests both functionality and backward compatibility
"""

import asyncio
import sys
import traceback
from typing import Dict, Any

# Test results tracking
test_results = {
    "passed": [],
    "failed": [],
    "warnings": []
}

def test_import_modules():
    """Test 1: Import all new modules"""
    print("\n🔧 Test 1: Module Imports")
    try:
        from app.services.llm_service import LLMService, get_llm_service, TaskPromptBuilder
        print("  ✓ LLM service imports")
        test_results["passed"].append("LLM service imports")
        
        from app.execution.async_executor import AsyncTaskExecutor, AsyncExecutionOrchestrator
        print("  ✓ Async executor imports")
        test_results["passed"].append("Async executor imports")
        
        from app.routers.async_execution_routes import router
        print("  ✓ Async routes imports")
        test_results["passed"].append("Async routes imports")
        
        return True
    except Exception as e:
        print(f"  ✗ Import failed: {e}")
        test_results["failed"].append(f"Module imports: {str(e)}")
        return False

def test_llm_service():
    """Test 2: LLM Service functionality"""
    print("\n🔧 Test 2: LLM Service")
    try:
        from app.services.llm_service import get_llm_service, TaskPromptBuilder
        
        # Test service instantiation
        service = get_llm_service()
        print("  ✓ LLM service instantiation")
        test_results["passed"].append("LLM service instantiation")
        
        # Test prompt builder
        builder = TaskPromptBuilder()
        
        # Test initial prompt
        prompt = builder.build_initial_prompt("Test Task", context="Test context")
        assert "Test Task" in prompt
        assert "Test context" in prompt
        print("  ✓ Initial prompt builder")
        test_results["passed"].append("Initial prompt builder")
        
        # Test revision prompt
        revision = builder.build_revision_prompt(
            "Test Task", 
            "Current content",
            ["Feedback 1", "Feedback 2"]
        )
        assert "Test Task" in revision
        assert "Current content" in revision
        assert "Feedback 1" in revision
        print("  ✓ Revision prompt builder")
        test_results["passed"].append("Revision prompt builder")
        
        # Test evaluation prompt
        eval_prompt = builder.build_evaluation_prompt("Content", "Task")
        assert "Content" in eval_prompt
        assert "Task" in eval_prompt
        print("  ✓ Evaluation prompt builder")
        test_results["passed"].append("Evaluation prompt builder")
        
        return True
    except Exception as e:
        print(f"  ✗ LLM Service test failed: {e}")
        test_results["failed"].append(f"LLM Service: {str(e)}")
        traceback.print_exc()
        return False

def test_backward_compatibility():
    """Test 3: Backward compatibility with existing executors"""
    print("\n🔧 Test 3: Backward Compatibility")
    try:
        from app.execution.base_executor import BaseTaskExecutor
        
        # Test base executor still works
        executor = BaseTaskExecutor()
        print("  ✓ Base executor instantiation")
        test_results["passed"].append("Base executor compatibility")
        
        # Test that executor has required methods
        assert hasattr(executor, 'execute_llm_chat')
        assert hasattr(executor, 'get_task_id_and_name')
        assert hasattr(executor, 'fetch_prompt')
        print("  ✓ Base executor methods intact")
        test_results["passed"].append("Base executor methods")
        
        # Test enhanced executor
        from app.execution.executors.enhanced import execute_task_with_evaluation
        print("  ✓ Enhanced executor imports")
        test_results["passed"].append("Enhanced executor compatibility")
        
        return True
    except Exception as e:
        print(f"  ✗ Backward compatibility failed: {e}")
        test_results["failed"].append(f"Backward compatibility: {str(e)}")
        return False

async def test_async_executor():
    """Test 4: Async executor functionality"""
    print("\n🔧 Test 4: Async Executor")
    try:
        from app.execution.async_executor import AsyncTaskExecutor
        from app.repository.tasks import default_repo
        
        # Create executor
        executor = AsyncTaskExecutor(max_concurrent=2)
        print("  ✓ Async executor instantiation")
        test_results["passed"].append("Async executor instantiation")
        
        # Test task info extraction
        test_task = {"id": 1, "name": "Test Task"}
        task_id, task_name = executor._extract_task_info(test_task)
        assert task_id == 1
        assert task_name == "Test Task"
        print("  ✓ Task info extraction")
        test_results["passed"].append("Task info extraction")
        
        # Test semaphore configuration
        assert executor.max_concurrent == 2
        assert executor.semaphore._value == 2
        print("  ✓ Concurrency control")
        test_results["passed"].append("Concurrency control")
        
        return True
    except Exception as e:
        print(f"  ✗ Async executor test failed: {e}")
        test_results["failed"].append(f"Async executor: {str(e)}")
        return False

def test_async_routes():
    """Test 5: Async routes configuration"""
    print("\n🔧 Test 5: Async Routes")
    try:
        from app.routers import get_all_routers
        
        routers = get_all_routers()
        
        # Check that async router is included
        router_prefixes = [r.prefix for r in routers if hasattr(r, 'prefix')]
        assert "/async" in router_prefixes
        print("  ✓ Async router registered")
        test_results["passed"].append("Async router registration")
        
        # Check route count increased
        assert len(routers) >= 10  # Should have at least 10 routers now
        print(f"  ✓ Total routers: {len(routers)}")
        test_results["passed"].append("Router count")
        
        return True
    except Exception as e:
        print(f"  ✗ Async routes test failed: {e}")
        test_results["failed"].append(f"Async routes: {str(e)}")
        return False

def test_database_operations():
    """Test 6: Database operations still work"""
    print("\n🔧 Test 6: Database Operations")
    try:
        from app.repository.tasks import default_repo
        from app.database_pool import get_db
        
        # Test database connection
        with get_db() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM tasks")
            count = cursor.fetchone()[0]
            print(f"  ✓ Database connection (tasks: {count})")
            test_results["passed"].append("Database connection")
        
        # Test repository methods
        assert hasattr(default_repo, 'get_task_info')
        assert hasattr(default_repo, 'update_task_status')
        assert hasattr(default_repo, 'upsert_task_output')
        print("  ✓ Repository methods available")
        test_results["passed"].append("Repository methods")
        
        return True
    except Exception as e:
        print(f"  ✗ Database test failed: {e}")
        test_results["failed"].append(f"Database operations: {str(e)}")
        return False

async def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("🧪 REFACTORING TEST SUITE")
    print("=" * 60)
    
    # Run synchronous tests
    test_import_modules()
    test_llm_service()
    test_backward_compatibility()
    test_database_operations()
    test_async_routes()
    
    # Run async tests
    await test_async_executor()
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)
    
    total_passed = len(test_results["passed"])
    total_failed = len(test_results["failed"])
    total_warnings = len(test_results["warnings"])
    
    print(f"\n✅ Passed: {total_passed}")
    for test in test_results["passed"]:
        print(f"   • {test}")
    
    if test_results["failed"]:
        print(f"\n❌ Failed: {total_failed}")
        for test in test_results["failed"]:
            print(f"   • {test}")
    
    if test_results["warnings"]:
        print(f"\n⚠️ Warnings: {total_warnings}")
        for warning in test_results["warnings"]:
            print(f"   • {warning}")
    
    print("\n" + "=" * 60)
    
    if total_failed == 0:
        print("✅ ALL TESTS PASSED! Safe to commit.")
        return True
    else:
        print("❌ SOME TESTS FAILED! Please fix issues before committing.")
        return False

if __name__ == "__main__":
    # Run tests
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)