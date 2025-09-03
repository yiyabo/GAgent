#!/usr/bin/env python3
"""
Test Multi-Expert Evaluation System
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from app.models import EvaluationConfig
from app.services.expert_evaluator import get_multi_expert_evaluator


def test_multi_expert_evaluator():
    """Test multi-expert evaluator with sample bacteriophage content"""

    print("🎭 Testing Multi-Expert Evaluator System...")

    # Create evaluator
    config = EvaluationConfig(
        quality_threshold=0.7,
        evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"],
    )
    evaluator = get_multi_expert_evaluator(config)

    # Test content about bacteriophage therapy
    test_content = """
    噬菌体治疗是一种创新的抗菌策略，利用噬菌体的特异性来靶向杀死致病细菌。
    这种治疗方法在对抗多重耐药菌感染方面显示出巨大潜力。
    
    噬菌体具有以下优势：
    1. 高度特异性，不会影响正常菌群
    2. 能够进化以对抗细菌耐药性
    3. 副作用相对较少
    
    在临床应用中，噬菌体治疗需要考虑以下因素：
    - 患者免疫反应
    - 噬菌体的稳定性和活性
    - 给药途径和剂量优化
    
    目前多项临床试验正在进行中，初步结果显示噬菌体治疗在治疗铜绿假单胞菌、
    金黄色葡萄球菌等感染方面具有良好的安全性和有效性。
    """

    task_context = {"name": "评估噬菌体治疗的临床应用前景和挑战", "task_type": "clinical_analysis"}

    print(f"📝 测试内容长度: {len(test_content)} 字符")
    print(f"🎯 任务背景: {task_context['name']}")

    # Test with selected experts
    selected_experts = ["theoretical_biologist", "clinical_physician", "regulatory_expert"]
    print(f"👥 选择的专家: {', '.join(selected_experts)}")

    try:
        # Multi-expert evaluation
        result = evaluator.evaluate_with_multiple_experts(
            content=test_content, task_context=task_context, selected_experts=selected_experts, iteration=1
        )

        print("\n🎭 多专家评估结果:")

        # Individual expert results
        expert_evals = result.get("expert_evaluations", {})
        print(f"   成功评估专家数: {len(expert_evals)}")

        for expert_name, evaluation in expert_evals.items():
            print(f"\n   👤 {evaluation.get('expert_role', expert_name)}:")
            print(f"      总体评分: {evaluation.get('overall_score', 0):.3f}")
            print(f"      相关性: {evaluation.get('relevance', 0):.3f}")
            print(f"      完整性: {evaluation.get('completeness', 0):.3f}")
            print(f"      准确性: {evaluation.get('accuracy', 0):.3f}")
            print(f"      实用性: {evaluation.get('practicality', 0):.3f}")
            print(f"      创新性: {evaluation.get('innovation', 0):.3f}")
            print(f"      风险评估: {evaluation.get('risk_assessment', 0):.3f}")
            print(f"      信心度: {evaluation.get('confidence_level', 0):.3f}")

            # Show key insights
            strengths = evaluation.get("key_strengths", [])
            concerns = evaluation.get("major_concerns", [])
            suggestions = evaluation.get("specific_suggestions", [])

            if strengths:
                print(f"      ✅ 主要优势: {strengths[0] if strengths else '无'}")
            if concerns:
                print(f"      ⚠️  主要关切: {concerns[0] if concerns else '无'}")
            if suggestions:
                print(f"      💡 改进建议: {suggestions[0] if suggestions else '无'}")

        # Consensus results
        consensus = result.get("consensus", {})
        print(f"\n🤝 专家共识:")
        print(f"   综合评分: {consensus.get('overall_score', 0):.3f}")
        print(f"   共识信心度: {consensus.get('consensus_confidence', 0):.3f}")
        print(f"   参与专家数: {consensus.get('expert_count', 0)}")

        # Disagreements
        disagreements = result.get("disagreements", [])
        if disagreements:
            print(f"\n🔥 专家分歧:")
            for disagreement in disagreements:
                print(
                    f"   {disagreement['field']}: {disagreement['lowest_scorer']}({disagreement['lowest_score']:.2f}) vs {disagreement['highest_scorer']}({disagreement['highest_score']:.2f})"
                )
        else:
            print(f"\n✅ 专家意见一致，无重大分歧")

        # Metadata
        metadata = result.get("metadata", {})
        print(f"\n📊 评估统计:")
        print(f"   成功率: {metadata.get('successful_experts', 0)}/{metadata.get('total_experts', 0)}")
        print(f"   评估方法: {metadata.get('evaluation_method', 'unknown')}")

        # Success criteria
        success = (
            len(expert_evals) >= 2  # At least 2 experts evaluated
            and consensus.get("overall_score", 0) > 0.1  # Some meaningful score
            and consensus.get("consensus_confidence", 0) > 0.3  # Reasonable confidence
        )

        assert (
            success
        ), f"Multi-expert evaluation failed: experts={len(expert_evals)}, consensus_score={consensus.get('overall_score', 0):.3f}, confidence={consensus.get('consensus_confidence', 0):.3f}"

    except Exception as e:
        print(f"❌ 多专家评估失败: {e}")
        import traceback

        traceback.print_exc()
        assert False, f"Multi-expert evaluation failed with error: {e}"


def test_expert_roles():
    """Test individual expert role definitions"""

    print("\n🔍 Testing Expert Role Definitions...")

    evaluator = get_multi_expert_evaluator()

    print(f"   专家角色数量: {len(evaluator.experts)}")

    for name, expert in evaluator.experts.items():
        print(f"   👤 {expert.name} (权重: {expert.weight})")
        print(f"      描述: {expert.description}")
        print(f"      关注领域: {', '.join(expert.focus_areas)}")
        print()


if __name__ == "__main__":
    print("🎭 Multi-Expert Evaluation System Test")
    print("=" * 50)

    try:
        # Test expert role definitions
        test_expert_roles()

        # Test multi-expert evaluation
        test_multi_expert_evaluator()

        print("\n" + "=" * 50)
        print("🎯 测试结果: ✅ 成功")

    except AssertionError as e:
        print(f"\n🎯 测试结果: ❌ 失败 - {e}")
    except Exception as e:
        print(f"\n🎯 测试结果: ❌ 错误 - {e}")
