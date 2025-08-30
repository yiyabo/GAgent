"""
Integration test for the complete evaluation system
"""

import sys
import os
from unittest.mock import patch
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import default_repo
from app.execution.executors.enhanced import execute_task_with_evaluation
from app.models import EvaluationConfig


@patch.dict(os.environ, {'LLM_MOCK': '1'})
def test_complete_evaluation_system():
    """Test the complete evaluation system integration"""
    print("Testing complete evaluation system...")
    
    # Reload config to pick up mock mode from env var
    from app.services.config import reload_config
    reload_config()
    
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
    
    # Mock the LLM client at the base executor level
    from app.execution.base_executor import BaseTaskExecutor
    
    # Create a mock client
    from unittest.mock import Mock
    mock_client = Mock()
    mock_response = Mock()
    mock_choice = Mock()
    mock_message = Mock()
    
    def mock_create(**kwargs):
        mock_message.content = mock_llm_chat(kwargs.get('messages', [{}])[0].get('content', ''))
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        return mock_response
    
    mock_client.chat.completions.create = mock_create
    
    # Patch the client creation in BaseTaskExecutor
    original_init = BaseTaskExecutor.__init__
    def patched_init(self, repo=None):
        original_init(self, repo)
        self.client = mock_client
    BaseTaskExecutor.__init__ = patched_init
    
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
        assert latest["iteration"] == result.iterations - 1  # iteration是从0开始，而result.iterations是总数
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
        # Restore original BaseTaskExecutor.__init__
        BaseTaskExecutor.__init__ = original_init


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