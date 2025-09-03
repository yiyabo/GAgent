#!/usr/bin/env python3
"""
Quick test for LLM Evaluator
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from app.models import EvaluationConfig
from app.services.llm_evaluator import get_llm_evaluator


def test_llm_evaluator():
    """Test LLM evaluator with sample content"""

    print("🧪 Testing LLM Evaluator...")

    # Create evaluator
    config = EvaluationConfig(
        quality_threshold=0.7,
        evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"],
    )
    evaluator = get_llm_evaluator(config)

    # Test content
    test_content = """
    噬菌体治疗是一种利用噬菌体(bacteriophage)来对抗细菌感染的新兴治疗方法。
    噬菌体是一种能够感染和杀死细菌的病毒，具有高度的特异性。
    这种治疗方法在抗生素耐药性日益严重的今天显得尤为重要。
    研究表明，噬菌体治疗可以有效地治疗多种细菌感染，包括铜绿假单胞菌感染。
    """

    task_context = {"name": "介绍噬菌体治疗的基本原理和应用前景", "task_type": "content_generation"}

    print(f"📝 测试内容: {test_content[:50]}...")
    print(f"🎯 任务背景: {task_context['name']}")

    try:
        # Evaluate
        result = evaluator.evaluate_content_intelligent(test_content, task_context, 1)

        print("\n✅ LLM 评估结果:")
        print(f"   总体评分: {result.overall_score:.3f}")
        print(f"   相关性: {result.dimensions.relevance:.3f}")
        print(f"   完整性: {result.dimensions.completeness:.3f}")
        print(f"   准确性: {result.dimensions.accuracy:.3f}")
        print(f"   清晰度: {result.dimensions.clarity:.3f}")
        print(f"   连贯性: {result.dimensions.coherence:.3f}")
        print(f"   科学严谨性: {result.dimensions.scientific_rigor:.3f}")

        print(f"\n💡 改进建议:")
        for i, suggestion in enumerate(result.suggestions, 1):
            print(f"   {i}. {suggestion}")

        print(f"\n🔄 需要修订: {'是' if result.needs_revision else '否'}")
        print(f"⏰ 评估方法: {result.metadata.get('evaluation_method', 'unknown')}")

        assert result.overall_score > 0.5, f"LLM evaluation score too low: {result.overall_score:.3f}"

    except Exception as e:
        print(f"❌ 评估失败: {e}")
        assert False, f"LLM evaluation failed with error: {e}"


if __name__ == "__main__":
    try:
        test_llm_evaluator()
        print(f"\n🎯 测试结果: ✅ 成功")
    except AssertionError as e:
        print(f"\n🎯 测试结果: ❌ 失败 - {e}")
    except Exception as e:
        print(f"\n🎯 测试结果: ❌ 错误 - {e}")
