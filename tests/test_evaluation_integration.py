"""
Integration test for the complete evaluation system
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import default_repo
from app.executor_enhanced import execute_task_with_evaluation
from app.models import EvaluationConfig


def test_complete_evaluation_system():
    """Test the complete evaluation system integration"""
    print("Testing complete evaluation system...")
    
    # Initialize database
    init_db()
    print("✓ Database initialized")
    
    # Create a test task
    task_id = default_repo.create_task(
        name="Write a comprehensive introduction to bacteriophage therapy",
        status="pending",
        task_type="atomic"
    )
    print(f"✓ Created test task {task_id}")
    
    # Set evaluation configuration
    default_repo.store_evaluation_config(
        task_id=task_id,
        quality_threshold=0.7,  # Lower threshold for testing
        max_iterations=2,
        evaluation_dimensions=["relevance", "completeness", "accuracy"],
        domain_specific=True,
        strict_mode=False
    )
    print("✓ Evaluation config stored")
    
    # Execute task with evaluation (mocked LLM)
    def mock_llm_chat(prompt):
        if "revision" in prompt.lower() or "improve" in prompt.lower():
            # Second iteration - better content
            return """
            Bacteriophage Therapy: A Comprehensive Introduction
            
            Bacteriophage therapy represents a revolutionary approach to treating bacterial infections using naturally occurring viruses called bacteriophages. These viruses specifically target and destroy pathogenic bacteria while leaving beneficial microorganisms intact.
            
            The mechanism involves highly specific recognition where phages bind to bacterial surface receptors, inject their genetic material, and hijack bacterial machinery for replication. This process ultimately leads to bacterial cell lysis and death.
            
            Clinical applications have shown promising results in treating antibiotic-resistant infections, including MRSA and Pseudomonas aeruginosa. Recent studies demonstrate efficacy rates of 60-80% in complex cases.
            
            Future research focuses on standardization protocols, regulatory frameworks, and combination therapies to maximize therapeutic potential.
            """
        else:
            # First iteration - poor content
            return "Bacteriophages are viruses that kill bacteria. They might be useful for medicine."
    
    # Mock the LLM client
    import app.executor_enhanced
    original_glm_chat = app.executor_enhanced._glm_chat
    app.executor_enhanced._glm_chat = mock_llm_chat
    
    try:
        # Get task info
        task = default_repo.get_task_info(task_id)
        
        # Execute with evaluation
        result = execute_task_with_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=2,
            quality_threshold=0.7,
            use_context=False
        )
        
        print(f"✓ Task execution completed: {result.status}")
        print(f"  Final score: {result.evaluation.overall_score:.3f}")
        print(f"  Iterations: {result.iterations}")
        print(f"  Execution time: {result.execution_time:.2f}s")
        
        # Verify evaluation history
        history = default_repo.get_evaluation_history(task_id)
        print(f"✓ Evaluation history: {len(history)} iterations")
        
        for i, eval_record in enumerate(history, 1):
            print(f"  Iteration {i}: score={eval_record['overall_score']:.3f}, needs_revision={eval_record['needs_revision']}")
        
        # Test latest evaluation retrieval
        latest = default_repo.get_latest_evaluation(task_id)
        assert latest is not None
        assert latest["iteration"] == result.iterations
        print("✓ Latest evaluation retrieval works")
        
        # Test evaluation config retrieval
        config = default_repo.get_evaluation_config(task_id)
        assert config["quality_threshold"] == 0.7
        assert config["max_iterations"] == 2
        print("✓ Evaluation config retrieval works")
        
        # Test evaluation stats
        stats = default_repo.get_evaluation_stats()
        assert stats["total_evaluations"] >= 2
        print(f"✓ Evaluation stats: {stats['total_evaluations']} total evaluations, avg score: {stats['average_score']:.3f}")
        
        # Test task output
        task_output = default_repo.get_task_output_content(task_id)
        assert task_output is not None
        assert len(task_output) > 100  # Should have substantial content
        print("✓ Task output properly stored")
        
        # Verify the task improved across iterations
        if len(history) >= 2:
            first_score = history[0]["overall_score"]
            last_score = history[-1]["overall_score"]
            improvement = last_score > first_score
            print(f"✓ Content improvement: {first_score:.3f} → {last_score:.3f} {'(improved!)' if improvement else '(no improvement)'}")
        
        # Clean up
        default_repo.delete_evaluation_history(task_id)
        print("✓ Cleanup completed")
        
        print("\n🎉 Complete evaluation system test passed!")
        
    finally:
        # Restore original LLM function
        app.executor_enhanced._glm_chat = original_glm_chat


def test_evaluation_edge_cases():
    """Test edge cases and error handling"""
    print("\nTesting evaluation edge cases...")
    
    # Test with non-existent task
    try:
        result = default_repo.get_evaluation_history(99999)
        assert result == []
        print("✓ Non-existent task evaluation history returns empty list")
    except Exception as e:
        print(f"✗ Failed non-existent task test: {e}")
        assert False, f"Non-existent task test failed: {e}"
    
    # Test evaluation config defaults
    try:
        config = default_repo.get_evaluation_config(99999)
        assert config is None
        print("✓ Non-existent task config returns None")
    except Exception as e:
        print(f"✗ Failed config test: {e}")
        assert False, f"Config test failed: {e}"
    
    print("✓ Edge case tests passed!")


if __name__ == "__main__":
    test_complete_evaluation_system()
    test_evaluation_edge_cases()
    
    print("\n🌟 All evaluation system tests passed! 🌟")