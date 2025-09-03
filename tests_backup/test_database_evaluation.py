"""
Test database evaluation functionality
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import default_repo


def test_evaluation_database():
    """Test evaluation-related database operations"""
    print("Testing evaluation database functionality...")

    # Initialize database with new tables
    init_db()
    print("✓ Database initialized with evaluation tables")

    # First create a test task (required for foreign key constraint)
    task_id = default_repo.create_task(name="Test evaluation task", status="pending", task_type="atomic")
    print(f"✓ Created test task {task_id}")

    # Test storing evaluation config
    default_repo.store_evaluation_config(
        task_id=task_id,
        quality_threshold=0.85,
        max_iterations=4,
        evaluation_dimensions=["relevance", "completeness", "accuracy"],
        domain_specific=True,
        strict_mode=False,
        custom_weights={"relevance": 0.4, "completeness": 0.3, "accuracy": 0.3},
    )
    print("✓ Evaluation config stored")

    # Test retrieving evaluation config
    config = default_repo.get_evaluation_config(task_id)
    assert config is not None
    assert config["quality_threshold"] == 0.85
    assert config["max_iterations"] == 4
    assert config["domain_specific"] == True
    print("✓ Evaluation config retrieved successfully")

    # Test storing evaluation history
    history_id = default_repo.store_evaluation_history(
        task_id=task_id,
        iteration=1,
        content="Test content for evaluation",
        overall_score=0.75,
        dimension_scores={"relevance": 0.8, "completeness": 0.7, "accuracy": 0.75},
        suggestions=["Improve clarity", "Add more details"],
        needs_revision=True,
        metadata={"test": True},
    )
    assert history_id > 0
    print("✓ Evaluation history stored")

    # Test retrieving evaluation history
    history = default_repo.get_evaluation_history(task_id)
    assert len(history) == 1
    assert history[0]["overall_score"] == 0.75
    assert history[0]["iteration"] == 1
    assert len(history[0]["suggestions"]) == 2
    print("✓ Evaluation history retrieved successfully")

    # Test latest evaluation
    latest = default_repo.get_latest_evaluation(task_id)
    assert latest is not None
    assert latest["overall_score"] == 0.75
    print("✓ Latest evaluation retrieved")

    # Test storing multiple iterations
    default_repo.store_evaluation_history(
        task_id=task_id,
        iteration=2,
        content="Improved test content",
        overall_score=0.85,
        dimension_scores={"relevance": 0.9, "completeness": 0.8, "accuracy": 0.85},
        suggestions=["Minor improvements needed"],
        needs_revision=False,
    )

    # Check that latest evaluation is updated
    latest = default_repo.get_latest_evaluation(task_id)
    assert latest["iteration"] == 2
    assert latest["overall_score"] == 0.85
    print("✓ Multiple iterations handling works")

    # Test evaluation stats
    stats = default_repo.get_evaluation_stats()
    assert stats["total_evaluations"] >= 2
    assert stats["average_score"] > 0
    print(f"✓ Evaluation stats: {stats['total_evaluations']} evaluations, avg score: {stats['average_score']:.3f}")

    # Clean up test data
    default_repo.delete_evaluation_history(task_id)
    history_after_cleanup = default_repo.get_evaluation_history(task_id)
    assert len(history_after_cleanup) == 0
    print("✓ Evaluation history cleanup successful")

    # Also clean up the test task
    # Note: Due to foreign key constraints, evaluation data should be automatically deleted
    # when the parent task is deleted, but we already deleted it manually above
    print("✓ All cleanup successful")

    print("All database tests passed! ✅")


if __name__ == "__main__":
    test_evaluation_database()
