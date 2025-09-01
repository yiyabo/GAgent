"""
Simple test for content evaluator without complex imports
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.content_evaluator import ContentEvaluator


def test_basic_evaluator():
    """Basic test of content evaluator"""
    print("Testing basic evaluator functionality...")

    # Test initialization
    evaluator = ContentEvaluator()
    assert evaluator.config.quality_threshold == 0.8
    print("✓ Evaluator initialization works")

    # Test empty content evaluation
    result = evaluator.evaluate_content("", {"name": "test task"})
    assert result.overall_score == 0.0
    assert result.needs_revision
    print("✓ Empty content evaluation works")

    # Test good content evaluation
    good_content = """
    Introduction to Bacteriophages
    
    Bacteriophages are viruses that specifically infect bacteria. They play a crucial role in bacterial ecology and have significant potential for therapeutic applications. These viruses consist of a protein capsid containing genetic material, typically DNA or RNA.
    
    Phages exhibit remarkable diversity in their morphology and replication strategies. They can be classified into different categories based on their life cycles, including lytic and lysogenic phages. Recent research has demonstrated their effectiveness in treating antibiotic-resistant bacterial infections.
    
    In conclusion, bacteriophages represent a promising avenue for developing new antimicrobial therapies, particularly in the era of increasing antibiotic resistance.
    """

    task_context = {
        "name": "Write an introduction to bacteriophages",
        "task_type": "atomic",
    }

    result = evaluator.evaluate_content(good_content, task_context)
    print(f"✓ Good content evaluation: score={result.overall_score:.3f}")

    # Test dimension evaluation
    assert hasattr(result.dimensions, "relevance")
    assert hasattr(result.dimensions, "completeness")
    assert hasattr(result.dimensions, "accuracy")
    print(
        f"✓ Dimensions evaluated: relevance={result.dimensions.relevance:.3f}, completeness={result.dimensions.completeness:.3f}"
    )

    # Test poor content evaluation
    poor_content = "Phages are things."
    result_poor = evaluator.evaluate_content(poor_content, task_context)
    print(f"✓ Poor content evaluation: score={result_poor.overall_score:.3f}")

    # Good content should score higher than poor content
    assert result.overall_score > result_poor.overall_score
    print("✓ Quality differentiation works")

    print("All basic tests passed! ✅")


if __name__ == "__main__":
    test_basic_evaluator()
