#!/usr/bin/env python3
"""
评估系统使用示例

本文件包含了各种评估模式的实际使用示例，帮助用户快速上手和理解系统功能。
"""

import sys
import os
import time
from typing import Dict, Any, List

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.execution.executors.enhanced import (
    execute_task_with_evaluation,
    execute_task_with_llm_evaluation,
    execute_task_with_multi_expert_evaluation,
    execute_task_with_adversarial_evaluation
)
from app.models import EvaluationConfig
from app.repository.tasks import default_repo
from app.services.evaluation_supervisor import get_evaluation_supervisor, get_supervision_report
from app.services.evaluation_cache import get_evaluation_cache
from app.services.meta_evaluator import get_meta_evaluator
from app.services.phage_evaluator import get_phage_evaluator


def example_basic_evaluation():
    """示例1: 基础评估"""
    print("=" * 60)
    print("示例1: 基础评估")
    print("=" * 60)
    
    # 创建示例任务
    task = {
        "id": 1001,
        "name": "编写噬菌体治疗的基础介绍",
        "content": ""
    }
    
    # 配置评估参数
    config = EvaluationConfig(
        quality_threshold=0.7,
        max_iterations=3,
        evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity"]
    )
    
    print(f"任务: {task['name']}")
    print(f"质量阈值: {config.quality_threshold}")
    print(f"最大迭代: {config.max_iterations}")
    print()
    
    try:
        # 执行基础评估
        start_time = time.time()
        result = execute_task_with_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=config.max_iterations,
            quality_threshold=config.quality_threshold,
            evaluation_config=config
        )
        execution_time = time.time() - start_time
        
        # 显示结果
        print("✅ 基础评估完成!")
        print(f"最终状态: {result.status}")
        print(f"最终评分: {result.evaluation.overall_score:.3f}")
        print(f"完成迭代: {result.iterations}")
        print(f"执行时间: {execution_time:.2f}秒")
        
        if result.evaluation.suggestions:
            print("\n改进建议:")
            for i, suggestion in enumerate(result.evaluation.suggestions[:3], 1):
                print(f"  {i}. {suggestion}")
        
        return result
        
    except Exception as e:
        print(f"❌ 基础评估失败: {e}")
        return None


def example_llm_intelligent_evaluation():
    """示例2: LLM智能评估"""
    print("\n" + "=" * 60)
    print("示例2: LLM智能评估")
    print("=" * 60)
    
    # 创建示例任务
    task = {
        "id": 1002,
        "name": "分析噬菌体在抗生素耐药性治疗中的应用前景",
        "content": ""
    }
    
    # 配置LLM评估参数
    config = EvaluationConfig(
        quality_threshold=0.8,
        max_iterations=3,
        strict_mode=True,
        evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
    )
    
    print(f"任务: {task['name']}")
    print(f"评估模式: LLM智能评估")
    print(f"质量阈值: {config.quality_threshold}")
    print(f"严格模式: {config.strict_mode}")
    print()
    
    try:
        # 执行LLM智能评估
        start_time = time.time()
        result = execute_task_with_llm_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=config.max_iterations,
            quality_threshold=config.quality_threshold,
            evaluation_config=config,
            use_context=True
        )
        execution_time = time.time() - start_time
        
        # 显示详细结果
        print("🧠 LLM智能评估完成!")
        print(f"最终状态: {result.status}")
        print(f"最终评分: {result.evaluation.overall_score:.3f}")
        print(f"完成迭代: {result.iterations_completed}")
        print(f"执行时间: {execution_time:.2f}秒")
        
        # 显示维度评分
        print("\n📊 维度评分:")
        dimensions = result.evaluation.dimensions
        print(f"  相关性: {dimensions.relevance:.3f}")
        print(f"  完整性: {dimensions.completeness:.3f}")
        print(f"  准确性: {dimensions.accuracy:.3f}")
        print(f"  清晰度: {dimensions.clarity:.3f}")
        print(f"  连贯性: {dimensions.coherence:.3f}")
        print(f"  科学严谨性: {dimensions.scientific_rigor:.3f}")
        
        # 显示智能建议
        if result.evaluation.suggestions:
            print("\n💡 智能改进建议:")
            for i, suggestion in enumerate(result.evaluation.suggestions[:3], 1):
                print(f"  {i}. {suggestion}")
        
        return result
        
    except Exception as e:
        print(f"❌ LLM智能评估失败: {e}")
        return None


def example_multi_expert_evaluation():
    """示例3: 多专家评估"""
    print("\n" + "=" * 60)
    print("示例3: 多专家评估")
    print("=" * 60)
    
    # 创建示例任务
    task = {
        "id": 1003,
        "name": "评估噬菌体疗法的临床试验设计方案",
        "content": ""
    }
    
    # 选择特定专家
    selected_experts = ["theoretical_biologist", "clinical_physician", "regulatory_expert"]
    
    print(f"任务: {task['name']}")
    print(f"评估模式: 多专家协作评估")
    print(f"参与专家: {', '.join(selected_experts)}")
    print()
    
    try:
        # 执行多专家评估
        start_time = time.time()
        result = execute_task_with_multi_expert_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=3,
            quality_threshold=0.8,
            selected_experts=selected_experts,
            use_context=True
        )
        execution_time = time.time() - start_time
        
        # 显示结果
        print("🎭 多专家评估完成!")
        print(f"最终状态: {result.status}")
        print(f"专家共识评分: {result.evaluation.overall_score:.3f}")
        print(f"完成迭代: {result.iterations_completed}")
        print(f"执行时间: {execution_time:.2f}秒")
        
        # 显示专家详情
        metadata = result.metadata or {}
        expert_evaluations = metadata.get('expert_evaluations', {})
        disagreements = metadata.get('disagreements', [])
        consensus_confidence = metadata.get('consensus_confidence', 0.0)
        
        if expert_evaluations:
            print("\n👥 各专家评分:")
            for expert_name, evaluation in expert_evaluations.items():
                expert_role = evaluation.get('expert_role', expert_name)
                overall_score = evaluation.get('overall_score', 0)
                confidence = evaluation.get('confidence_level', 0)
                print(f"  {expert_role}: {overall_score:.3f} (置信度: {confidence:.2f})")
            
            print(f"\n🤝 专家共识置信度: {consensus_confidence:.3f}")
        
        # 显示专家分歧
        if disagreements:
            print(f"\n🔥 专家分歧 ({len(disagreements)} 个领域):")
            for disagreement in disagreements[:3]:
                field = disagreement['field']
                level = disagreement['disagreement_level']
                lowest = disagreement['lowest_scorer']
                highest = disagreement['highest_scorer']
                print(f"  {field}: {lowest} vs {highest} (分歧度: {level:.2f})")
        else:
            print("\n✅ 专家意见高度一致")
        
        return result
        
    except Exception as e:
        print(f"❌ 多专家评估失败: {e}")
        return None


def example_adversarial_evaluation():
    """示例4: 对抗性评估"""
    print("\n" + "=" * 60)
    print("示例4: 对抗性评估 (生成器 vs 批评者)")
    print("=" * 60)
    
    # 创建示例任务
    task = {
        "id": 1004,
        "name": "制定噬菌体治疗的安全性评估标准",
        "content": ""
    }
    
    print(f"任务: {task['name']}")
    print(f"评估模式: 对抗性评估")
    print(f"最大轮数: 3")
    print(f"改进阈值: 0.1")
    print()
    
    try:
        # 执行对抗性评估
        start_time = time.time()
        result = execute_task_with_adversarial_evaluation(
            task=task,
            repo=default_repo,
            max_rounds=3,
            improvement_threshold=0.1,
            use_context=True
        )
        execution_time = time.time() - start_time
        
        # 显示结果
        print("⚔️ 对抗性评估完成!")
        print(f"最终状态: {result.status}")
        print(f"鲁棒性评分: {result.evaluation.overall_score:.3f}")
        print(f"完成轮数: {result.iterations_completed}")
        print(f"执行时间: {execution_time:.2f}秒")
        
        # 显示对抗性分析
        metadata = result.metadata or {}
        adversarial_effectiveness = metadata.get('adversarial_effectiveness', 0.0)
        robustness_score = metadata.get('robustness_score', 0.0)
        
        print(f"\n⚔️ 对抗性分析:")
        print(f"  对抗性效果: {adversarial_effectiveness:.3f}")
        print(f"  最终鲁棒性: {robustness_score:.3f}")
        
        # 显示对抗性洞察
        if result.evaluation.suggestions:
            print("\n💡 对抗性洞察:")
            for i, suggestion in enumerate(result.evaluation.suggestions[:3], 1):
                print(f"  {i}. {suggestion}")
        
        return result
        
    except Exception as e:
        print(f"❌ 对抗性评估失败: {e}")
        return None


def example_phage_domain_evaluation():
    """示例5: 噬菌体专业领域评估"""
    print("\n" + "=" * 60)
    print("示例5: 噬菌体专业领域评估")
    print("=" * 60)
    
    # 示例噬菌体研究内容
    phage_content = """
    噬菌体疗法是一种利用噬菌体（bacteriophage）来治疗细菌感染的新兴治疗方法。
    噬菌体是专门感染细菌的病毒，具有高度的宿主特异性。在临床应用中，
    我们需要考虑噬菌体的裂解周期、宿主范围、以及潜在的细菌耐药性问题。
    
    关键的安全性考虑包括：
    1. 噬菌体的基因组分析，确保不含有毒力基因
    2. 内毒素水平的检测和控制
    3. 免疫原性评估
    4. 长期稳定性研究
    
    监管方面，FDA已经批准了一些噬菌体产品用于食品安全，
    但治疗性噬菌体产品仍需要更严格的临床试验数据。
    """
    
    task_context = {
        "name": "噬菌体疗法安全性评估",
        "research_focus": "therapeutic_applications",
        "target_audience": "clinical_researchers"
    }
    
    print(f"评估内容: 噬菌体疗法专业文档")
    print(f"研究重点: {task_context['research_focus']}")
    print(f"目标受众: {task_context['target_audience']}")
    print()
    
    try:
        # 执行噬菌体专业评估
        phage_evaluator = get_phage_evaluator()
        
        start_time = time.time()
        result = phage_evaluator.evaluate_phage_content(
            content=phage_content,
            task_context=task_context
        )
        execution_time = time.time() - start_time
        
        # 显示结果
        print("🦠 噬菌体专业评估完成!")
        print(f"整体专业评分: {result['overall_score']:.3f}")
        print(f"执行时间: {execution_time:.2f}秒")
        
        # 显示专业维度评分
        print("\n📊 专业维度评分:")
        print(f"  术语准确性: {result['terminology_accuracy']:.3f}")
        print(f"  临床相关性: {result['clinical_relevance']:.3f}")
        print(f"  安全性评估: {result['safety_assessment']:.3f}")
        print(f"  研究方法: {result['research_methodology']:.3f}")
        
        # 显示专业建议
        if result.get('professional_suggestions'):
            print("\n💡 专业建议:")
            for i, suggestion in enumerate(result['professional_suggestions'][:3], 1):
                print(f"  {i}. {suggestion}")
        
        # 显示术语分析
        terminology_analysis = result.get('terminology_analysis', {})
        if terminology_analysis:
            correct_terms = terminology_analysis.get('correct_terms', [])
            questionable_terms = terminology_analysis.get('questionable_terms', [])
            
            if correct_terms:
                print(f"\n✅ 正确使用的专业术语: {', '.join(correct_terms[:5])}")
            if questionable_terms:
                print(f"\n⚠️ 需要确认的术语: {', '.join(questionable_terms[:3])}")
        
        return result
        
    except Exception as e:
        print(f"❌ 噬菌体专业评估失败: {e}")
        return None


def example_meta_cognitive_evaluation():
    """示例6: 元认知评估"""
    print("\n" + "=" * 60)
    print("示例6: 元认知评估 (评估的评估)")
    print("=" * 60)
    
    # 模拟评估历史
    evaluation_history = [
        {
            "iteration": 1,
            "overall_score": 0.65,
            "dimension_scores": {"relevance": 0.7, "completeness": 0.6, "accuracy": 0.65},
            "suggestions": ["增加更多细节", "改进逻辑结构"],
            "timestamp": "2024-01-01T10:00:00"
        },
        {
            "iteration": 2,
            "overall_score": 0.75,
            "dimension_scores": {"relevance": 0.8, "completeness": 0.7, "accuracy": 0.75},
            "suggestions": ["完善结论部分", "添加参考文献"],
            "timestamp": "2024-01-01T10:05:00"
        },
        {
            "iteration": 3,
            "overall_score": 0.82,
            "dimension_scores": {"relevance": 0.85, "completeness": 0.8, "accuracy": 0.8},
            "suggestions": ["优化表达方式"],
            "timestamp": "2024-01-01T10:10:00"
        }
    ]
    
    current_evaluation = evaluation_history[-1]
    task_context = {
        "name": "噬菌体治疗机制研究",
        "task_type": "scientific_analysis"
    }
    
    print(f"分析任务: {task_context['name']}")
    print(f"评估历史: {len(evaluation_history)} 次迭代")
    print(f"评分趋势: {evaluation_history[0]['overall_score']:.3f} → {evaluation_history[-1]['overall_score']:.3f}")
    print()
    
    try:
        # 执行元认知评估
        meta_evaluator = get_meta_evaluator()
        
        start_time = time.time()
        result = meta_evaluator.meta_evaluate_assessment_quality(
            evaluation_history=evaluation_history,
            task_context=task_context,
            current_evaluation=current_evaluation
        )
        execution_time = time.time() - start_time
        
        # 显示结果
        print("🧠 元认知评估完成!")
        print(f"评估质量评分: {result['assessment_quality_score']:.3f}")
        print(f"一致性评分: {result['consistency_score']:.3f}")
        print(f"执行时间: {execution_time:.2f}秒")
        
        # 显示认知偏见分析
        bias_analysis = result['cognitive_bias_analysis']
        print("\n🧠 认知偏见风险分析:")
        high_risk_biases = []
        for bias_type, risk_level in bias_analysis.items():
            risk_status = "高风险" if risk_level > 0.6 else "中风险" if risk_level > 0.3 else "低风险"
            color = "🔴" if risk_level > 0.6 else "🟡" if risk_level > 0.3 else "🟢"
            print(f"  {color} {bias_type}: {risk_level:.2f} ({risk_status})")
            if risk_level > 0.6:
                high_risk_biases.append(bias_type)
        
        # 显示元认知洞察
        meta_insights = result.get('meta_cognitive_insights', [])
        if meta_insights:
            print("\n💡 元认知洞察:")
            for i, insight in enumerate(meta_insights[:3], 1):
                print(f"  {i}. {insight}")
        
        # 高风险偏见警告
        if high_risk_biases:
            print(f"\n⚠️ 检测到高风险认知偏见: {', '.join(high_risk_biases)}")
            print("   建议采取措施减少偏见影响")
        
        return result
        
    except Exception as e:
        print(f"❌ 元认知评估失败: {e}")
        return None


def example_supervision_system():
    """示例7: 监督系统演示"""
    print("\n" + "=" * 60)
    print("示例7: 评估质量监督系统")
    print("=" * 60)
    
    try:
        # 获取监督报告
        print("🔍 获取系统监督报告...")
        supervision_report = get_supervision_report()
        
        # 显示系统健康状态
        system_health = supervision_report.get("system_health", {})
        overall_score = system_health.get("overall_score", 0.0)
        status = system_health.get("status", "unknown")
        
        print(f"\n📊 系统健康状态:")
        print(f"  整体健康评分: {overall_score:.3f}")
        print(f"  系统状态: {status.upper()}")
        
        # 显示当前质量指标
        current_metrics = supervision_report.get("current_metrics", {})
        if current_metrics:
            print(f"\n📈 当前质量指标:")
            for metric_name, metric_data in current_metrics.items():
                value = metric_data.get("value", 0.0)
                status = metric_data.get("status", "unknown")
                threshold = metric_data.get("threshold", 0.0)
                status_icon = "✅" if status == "good" else "⚠️" if status == "warning" else "❌"
                print(f"  {status_icon} {metric_name}: {value:.3f} (阈值: {threshold:.3f})")
        
        # 显示性能摘要
        performance_summary = supervision_report.get("performance_summary", {})
        if performance_summary:
            print(f"\n⚡ 性能摘要:")
            avg_time = performance_summary.get("avg_evaluation_time", 0.0)
            success_rate = performance_summary.get("success_rate", 0.0)
            cache_hit_rate = performance_summary.get("avg_cache_hit_rate", 0.0)
            print(f"  平均评估时间: {avg_time:.2f}秒")
            print(f"  成功率: {success_rate:.1%}")
            print(f"  缓存命中率: {cache_hit_rate:.1%}")
        
        # 显示最近警报
        alert_summary = supervision_report.get("alert_summary", {})
        total_alerts = alert_summary.get("total", 0)
        critical_alerts = alert_summary.get("critical", 0)
        
        if total_alerts > 0:
            print(f"\n🚨 最近24小时警报: {total_alerts} 个")
            if critical_alerts > 0:
                print(f"  其中严重警报: {critical_alerts} 个")
        else:
            print(f"\n✅ 最近24小时无警报")
        
        # 演示监督配置
        print(f"\n🔧 监督系统配置:")
        supervisor = get_evaluation_supervisor()
        
        # 更新一些阈值作为演示
        new_thresholds = {
            "min_accuracy": 0.75,
            "max_evaluation_time": 30.0
        }
        
        success = supervisor.update_thresholds(new_thresholds)
        if success:
            print(f"  ✅ 成功更新监督阈值:")
            for threshold_name, value in new_thresholds.items():
                print(f"    {threshold_name}: {value}")
        
        return supervision_report
        
    except Exception as e:
        print(f"❌ 监督系统演示失败: {e}")
        return None


def example_cache_optimization():
    """示例8: 缓存系统优化"""
    print("\n" + "=" * 60)
    print("示例8: 缓存系统和性能优化")
    print("=" * 60)
    
    try:
        # 获取缓存实例
        cache = get_evaluation_cache()
        
        # 显示缓存统计
        print("📊 缓存系统状态:")
        stats = cache.get_cache_stats()
        print(f"  缓存大小: {stats.get('cache_size', 0)} 条目")
        print(f"  命中率: {stats.get('hit_rate', 0.0):.1%}")
        print(f"  总查询数: {stats.get('total_queries', 0)}")
        print(f"  缓存命中数: {stats.get('cache_hits', 0)}")
        
        # 显示性能统计
        performance_stats = cache.get_performance_stats()
        if performance_stats:
            print(f"\n⚡ 性能统计:")
            print(f"  平均查询时间: {performance_stats.get('avg_query_time', 0.0):.3f}ms")
            print(f"  缓存效率: {performance_stats.get('cache_efficiency', 0.0):.1%}")
        
        # 演示缓存优化
        print(f"\n🔧 执行缓存优化...")
        optimization_result = cache.optimize_cache()
        
        print(f"  清理过期条目: {optimization_result.get('entries_removed', 0)} 个")
        print(f"  释放内存: {optimization_result.get('memory_freed', 0)} bytes")
        print(f"  优化后缓存大小: {optimization_result.get('final_cache_size', 0)} 条目")
        
        # 显示优化后的统计
        new_stats = cache.get_cache_stats()
        print(f"\n📈 优化后缓存状态:")
        print(f"  缓存大小: {new_stats.get('cache_size', 0)} 条目")
        print(f"  命中率: {new_stats.get('hit_rate', 0.0):.1%}")
        
        return {
            "before_optimization": stats,
            "optimization_result": optimization_result,
            "after_optimization": new_stats
        }
        
    except Exception as e:
        print(f"❌ 缓存优化演示失败: {e}")
        return None


def run_comprehensive_demo():
    """运行综合演示"""
    print("🚀 启动评估系统综合演示")
    print("=" * 80)
    
    results = {}
    
    # 运行各个示例
    examples = [
        ("basic_evaluation", example_basic_evaluation),
        ("llm_intelligent", example_llm_intelligent_evaluation),
        ("multi_expert", example_multi_expert_evaluation),
        ("adversarial", example_adversarial_evaluation),
        ("phage_domain", example_phage_domain_evaluation),
        ("meta_cognitive", example_meta_cognitive_evaluation),
        ("supervision", example_supervision_system),
        ("cache_optimization", example_cache_optimization)
    ]
    
    for example_name, example_func in examples:
        try:
            print(f"\n🔄 运行示例: {example_name}")
            result = example_func()
            results[example_name] = result
            
            if result:
                print(f"✅ 示例 {example_name} 完成")
            else:
                print(f"⚠️ 示例 {example_name} 未返回结果")
                
        except Exception as e:
            print(f"❌ 示例 {example_name} 执行失败: {e}")
            results[example_name] = None
        
        # 添加分隔符
        print("-" * 40)
    
    # 总结
    print("\n" + "=" * 80)
    print("📋 演示总结")
    print("=" * 80)
    
    successful = sum(1 for result in results.values() if result is not None)
    total = len(results)
    
    print(f"总示例数: {total}")
    print(f"成功执行: {successful}")
    print(f"成功率: {successful/total:.1%}")
    
    print(f"\n📊 各示例执行状态:")
    for example_name, result in results.items():
        status = "✅ 成功" if result is not None else "❌ 失败"
        print(f"  {example_name}: {status}")
    
    return results


if __name__ == "__main__":
    """主程序入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="评估系统使用示例")
    parser.add_argument("--example", type=str, choices=[
        "basic", "llm", "multi-expert", "adversarial", 
        "phage", "meta", "supervision", "cache", "all"
    ], default="all", help="选择要运行的示例")
    
    args = parser.parse_args()
    
    # 根据参数运行相应示例
    if args.example == "basic":
        example_basic_evaluation()
    elif args.example == "llm":
        example_llm_intelligent_evaluation()
    elif args.example == "multi-expert":
        example_multi_expert_evaluation()
    elif args.example == "adversarial":
        example_adversarial_evaluation()
    elif args.example == "phage":
        example_phage_domain_evaluation()
    elif args.example == "meta":
        example_meta_cognitive_evaluation()
    elif args.example == "supervision":
        example_supervision_system()
    elif args.example == "cache":
        example_cache_optimization()
    elif args.example == "all":
        run_comprehensive_demo()
    else:
        print("请选择有效的示例类型")
        parser.print_help()