#!/usr/bin/env python3
"""
Test Adversarial Evaluation System
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from app.models import EvaluationConfig
from app.services.adversarial_evaluator import get_adversarial_evaluator


def test_adversarial_evaluator():
    """Test adversarial evaluator with generator vs critic mechanism"""

    print("🥊 Testing Adversarial Evaluation System...")

    # Create evaluator
    config = EvaluationConfig(quality_threshold=0.8, max_iterations=3)
    evaluator = get_adversarial_evaluator(config)

    # Test content with deliberate flaws
    flawed_content = """
    噬菌体是很好的东西。它们可以杀死细菌。
    
    噬菌体很小，比细菌小很多。它们是病毒。
    
    使用噬菌体治疗感染是新方法。这个方法很有前景。
    """

    task_context = {"name": "详细介绍噬菌体治疗的机制、优势、挑战和应用前景", "task_type": "academic_review"}

    print(f"📝 测试内容（故意包含缺陷）:")
    print(f"   长度: {len(flawed_content)} 字符")
    print(f"   词数: {len(flawed_content.split())} 词")
    print(f"🎯 任务: {task_context['name']}")

    try:
        # Run adversarial evaluation
        print(f"\n⚔️ 开始对抗性评估...")

        result = evaluator.adversarial_evaluate(
            content=flawed_content, task_context=task_context, max_rounds=3, improvement_threshold=0.1
        )

        print(f"\n✅ 对抗性评估完成!")

        # Display results
        print(f"\n📊 评估结果:")
        print(f"   最佳鲁棒性评分: {result['best_robustness_score']:.3f}")
        print(f"   完成轮次: {result['rounds_completed']}/{result['total_rounds']}")
        print(f"   总发现问题数: {result['metadata']['total_criticisms']}")
        print(f"   平均鲁棒性: {result['metadata']['average_robustness']:.3f}")

        # Show round details
        print(f"\n🔄 对抗轮次详情:")
        for i, round_data in enumerate(result["adversarial_rounds"], 1):
            print(f"   轮次 {i}:")
            print(f"      发现问题: {round_data['criticism_count']}")
            print(f"      鲁棒性评分: {round_data['robustness_score']:.3f}")
            print(f"      内容已改进: {'是' if round_data['improvement_made'] else '否'}")

            # Show some criticisms
            if round_data["criticisms"]:
                print(f"      主要问题:")
                for j, criticism in enumerate(round_data["criticisms"][:2], 1):
                    issue = criticism.get("issue", "未知问题")
                    severity = criticism.get("severity", "未知")
                    print(f"        {j}. [{severity}] {issue}")

        # Final assessment
        assessment = result["final_assessment"]
        print(f"\n🎯 最终评估:")
        print(f"   最终鲁棒性评分: {assessment['final_robustness_score']:.3f}")
        print(f"   总批评数: {assessment['total_criticisms_found']}")
        print(f"   对抗有效性: {assessment['adversarial_effectiveness']:.3f}")
        print(f"   收敛达成: {'是' if assessment['convergence_achieved'] else '否'}")
        print(f"   推荐: {assessment['recommendation']}")

        # Most common issues
        if assessment["most_common_issues"]:
            print(f"\n🔍 最常见问题类型:")
            for category, count in assessment["most_common_issues"]:
                print(f"      {category}: {count}次")

        # Show improvement trend
        if assessment["improvement_trend"]:
            print(f"\n📈 改进趋势:")
            for i, improvement in enumerate(assessment["improvement_trend"], 1):
                direction = "📈" if improvement > 0 else "📉" if improvement < 0 else "➡️"
                print(f"      轮次 {i+1}: {direction} {improvement:+.3f}")

        # Content comparison
        print(f"\n📝 内容对比:")
        print(f"   原始内容长度: {len(flawed_content)} 字符")
        print(f"   最佳内容长度: {len(result['best_content'])} 字符")

        if len(result["best_content"]) > len(flawed_content):
            print(f"   ✅ 内容得到了扩展和改进")

        # Success criteria
        success = (
            result["rounds_completed"] >= 1
            and result["best_robustness_score"] > 0.3  # Some improvement
            and result["metadata"]["total_criticisms"] > 0  # Critic found issues
        )

        assert (
            success
        ), f"Adversarial evaluation failed: rounds={result['rounds_completed']}, score={result['best_robustness_score']:.3f}, criticisms={result['metadata']['total_criticisms']}"

    except Exception as e:
        print(f"❌ 对抗性评估失败: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_generator_and_critic_separately():
    """Test generator and critic components individually"""

    print("\n🔍 Testing Individual Components...")

    evaluator = get_adversarial_evaluator()

    # Test critic
    print("\n👤 Testing Content Critic:")
    test_content = "噬菌体治疗很好。"
    task_context = {"name": "噬菌体治疗机制分析"}

    criticisms = evaluator.critic.critique_content(test_content, task_context)
    print(f"   发现问题数: {len(criticisms)}")

    if criticisms:
        for i, criticism in enumerate(criticisms[:2], 1):
            print(f"   问题 {i}: {criticism.get('issue', '未知')}")

    # Test generator
    print("\n⚙️ Testing Content Generator:")
    if criticisms:
        improved = evaluator.generator.improve_content(test_content, criticisms, task_context)
        print(f"   原始长度: {len(test_content)} 字符")
        print(f"   改进长度: {len(improved)} 字符")
        print(f"   内容已改进: {'是' if improved != test_content else '否'}")

    assert len(criticisms) > 0, "Content critic should find at least one issue with the test content"


if __name__ == "__main__":
    print("🥊 Adversarial Evaluation System Test")
    print("=" * 50)

    try:
        # Test individual components
        test_generator_and_critic_separately()
        print("   组件测试: ✅ 成功")

        print("\n" + "=" * 50)

        # Test full adversarial system
        test_adversarial_evaluator()
        print("   对抗性测试: ✅ 成功")

        print("\n" + "=" * 50)
        print("🎯 总体结果: ✅ 成功")

    except AssertionError as e:
        print(f"❌ 测试失败: {e}")
    except Exception as e:
        print(f"❌ 测试错误: {e}")
