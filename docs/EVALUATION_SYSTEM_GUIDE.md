# 高级评估系统使用指南

## 概述

本系统提供了一套完整的内容评估和质量管理解决方案，包含多种先进的评估模式和自监督机制。系统支持从基础评估到复杂的多专家协作评估，适用于各种内容生成和质量控制场景。

## 核心特性

### 🧠 智能评估模式

- **LLM智能评估**: 基于大语言模型的深度语义理解评估
- **多专家评估**: 5位专业领域专家的协作评估系统
- **对抗性评估**: 生成器与批评者的对抗性改进机制
- **元认知评估**: 评估质量的自我反思和认知偏见检测
- **领域专业评估**: 针对噬菌体研究的专业术语和临床相关性评估

### ⚡ 性能优化

- **多层缓存系统**: 内存缓存 + SQLite持久化缓存
- **智能缓存策略**: 基于使用频率和时间的自动清理
- **性能监控**: 实时性能统计和优化建议

### 🔍 质量监督

- **自监督机制**: 自动检测评估系统质量下降
- **实时监控**: 准确性、一致性、性能等多维度监控
- **自动校准**: 基于历史数据的阈值自动调整
- **警报系统**: 多级别警报和推荐措施

## 快速开始

评估相关 CLI/REST 的常用命令与端到端示例已收敛到 `docs/QUICK_START.md`，本指南聚焦于配置、代码示例与最佳实践。

## 详细功能说明

### LLM智能评估器

LLM智能评估器使用大语言模型进行深度语义理解，提供6个维度的评估：

- **相关性 (Relevance)**: 内容与任务要求的匹配度
- **完整性 (Completeness)**: 内容的全面性和完整性
- **准确性 (Accuracy)**: 信息的准确性和可靠性
- **清晰度 (Clarity)**: 表达的清晰度和可理解性
- **连贯性 (Coherence)**: 逻辑结构和内容连贯性
- **科学严谨性 (Scientific Rigor)**: 科学方法和证据的严谨性

#### 使用示例

```python
from app.execution.executors.enhanced import execute_task_with_llm_evaluation
from app.models import EvaluationConfig

# 配置评估参数
config = EvaluationConfig(
    quality_threshold=0.8,
    max_iterations=3,
    strict_mode=True,
    evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
)

# 执行LLM智能评估
result = execute_task_with_llm_evaluation(
    task=task,
    evaluation_config=config,
    use_context=True
)

print(f"最终评分: {result.evaluation.overall_score:.3f}")
print(f"执行状态: {result.status}")
print(f"迭代次数: {result.iterations_completed}")
```

## 上下文策略与预算控制

评估与执行时可通过上下文和预算参数精细控制提示词上下文，兼顾质量与成本：

- 上下文收集：
  - **include_deps**: 是否包含依赖任务输出（默认 true）
  - **include_plan**: 是否包含同计划兄弟任务（默认 true）
  - **include_ancestors / include_siblings**: 是否包含祖先/同级（默认 false）
  - **semantic_k / min_similarity**: GLM 语义检索数量与相似度阈值（默认 5 / 0.1）
  - **hierarchy_k**: 层次检索数量（默认 3）

- 预算裁剪：
  - **max_chars**: 合并上下文的总字符预算（None 表示不裁剪）
  - **per_section_max**: 每个片段的最大字符数（None 表示不限制）
  - **strategy**: `truncate` 或 `sentence`（在有预算参数时生效）

请求示例（REST /tasks/{id}/context/preview）：

```json
{
  "include_deps": true,
  "include_plan": true,
  "semantic_k": 5,
  "min_similarity": 0.15,
  "include_ancestors": false,
  "include_siblings": false,
  "hierarchy_k": 3,
  "max_chars": 6000,
  "per_section_max": 1200,
  "strategy": "truncate"
}
```

严格评估建议：将 `quality_threshold` ≥ 0.92，`max_iterations` 设为 3-5，维度权重侧重 **accuracy** 与 **scientific_rigor**，可有效拉开不同配置的评分差异。

### 多专家评估系统

多专家评估系统模拟5位不同领域的专家进行协作评估：

1. **理论生物学家**: 关注科学理论和生物学原理
2. **临床医师**: 重视临床应用和患者安全
3. **监管专家**: 专注法规合规和安全标准
4. **研究科学家**: 强调研究方法和实验设计
5. **生物技术企业家**: 考虑商业可行性和市场应用

#### 专家评估流程

```python
from app.services.evaluation.expert_evaluator import get_multi_expert_evaluator

evaluator = get_multi_expert_evaluator()

# 多专家评估
result = evaluator.evaluate_with_multiple_experts(
    content="待评估内容",
    task_context={"name": "任务名称", "task_type": "content_generation"},
    selected_experts=["theoretical_biologist", "clinical_physician"],  # 可选：指定专家
    iteration=1
)

# 查看专家共识
consensus = result["consensus"]
print(f"专家共识评分: {consensus['overall_score']:.3f}")
print(f"共识置信度: {consensus['consensus_confidence']:.3f}")

# 查看专家分歧
disagreements = result["disagreements"]
for disagreement in disagreements:
    print(f"分歧领域: {disagreement['field']}")
    print(f"分歧程度: {disagreement['disagreement_level']:.2f}")
```

### 对抗性评估机制

对抗性评估采用生成器与批评者的对抗模式，通过多轮改进提升内容质量：

#### 对抗性评估组件

1. **内容生成器 (ContentGenerator)**: 负责生成和改进内容
2. **内容批评者 (ContentCritic)**: 负责发现问题和提供改进建议

#### 评估流程

```python
from app.services.evaluation.adversarial_evaluator import get_adversarial_evaluator

evaluator = get_adversarial_evaluator()

# 对抗性评估
result = evaluator.adversarial_evaluate(
    content="初始内容",
    task_context={"name": "任务名称"},
    max_rounds=3,
    improvement_threshold=0.1
)

print(f"最佳内容: {result['best_content']}")
print(f"鲁棒性评分: {result['best_robustness_score']:.3f}")
print(f"完成轮数: {result['rounds_completed']}")
print(f"对抗性效果: {result['final_assessment']['adversarial_effectiveness']:.3f}")
```

### 元认知评估系统

元认知评估系统对评估过程本身进行评估，检测认知偏见和评估质量：

#### 认知偏见检测

- **锚定偏见**: 过度依赖初始信息
- **确认偏见**: 倾向于确认既有观点
- **光环效应**: 整体印象影响具体判断
- **近因效应**: 过度重视最近信息
- **严重性偏见**: 对负面信息过度敏感

#### 使用示例

```python
from app.services.evaluation.meta_evaluator import get_meta_evaluator

meta_evaluator = get_meta_evaluator()

# 元认知评估
result = meta_evaluator.meta_evaluate_assessment_quality(
    evaluation_history=[eval1, eval2, eval3],
    task_context={"name": "任务名称"},
    current_evaluation=current_eval
)

print(f"评估质量评分: {result['assessment_quality_score']:.3f}")
print(f"一致性评分: {result['consistency_score']:.3f}")

# 查看认知偏见风险
bias_risks = result['cognitive_bias_analysis']
for bias_type, risk_level in bias_risks.items():
    if risk_level > 0.3:  # 高风险阈值
        print(f"检测到 {bias_type} 偏见风险: {risk_level:.2f}")
```

### 噬菌体专业评估器

专门针对噬菌体研究领域的专业评估器：

#### 评估维度

- **专业术语准确性**: 噬菌体相关术语的正确使用
- **临床相关性**: 与临床应用的相关程度
- **安全性评估**: 生物安全和监管合规性
- **研究方法**: 实验设计和研究方法的科学性

#### 使用示例

```python
from app.services.evaluation.phage_evaluator import get_phage_evaluator

phage_evaluator = get_phage_evaluator()

# 噬菌体专业评估
result = phage_evaluator.evaluate_phage_content(
    content="噬菌体研究内容",
    task_context={"research_focus": "therapeutic_applications"}
)

print(f"专业评估评分: {result['overall_score']:.3f}")
print(f"术语准确性: {result['terminology_accuracy']:.3f}")
print(f"临床相关性: {result['clinical_relevance']:.3f}")
print(f"安全性评估: {result['safety_assessment']:.3f}")
```

### 缓存和性能优化

系统提供多层缓存机制以提升性能：

#### 缓存配置

```python
from app.services.evaluation.evaluation_cache import get_evaluation_cache

cache = get_evaluation_cache()

# 查看缓存统计
stats = cache.get_cache_stats()
print(f"缓存命中率: {stats['hit_rate']:.1%}")
print(f"缓存大小: {stats['cache_size']} 条目")

# 优化缓存
optimization_result = cache.optimize_cache()
print(f"清理了 {optimization_result['entries_removed']} 个过期条目")

# 清空缓存
cache.clear_cache()
```

#### 性能监控

```python
# 查看性能统计
from app.services.evaluation.evaluation_cache import get_evaluation_cache

cache = get_evaluation_cache()
performance_stats = cache.get_performance_stats()

print(f"平均查询时间: {performance_stats['avg_query_time']:.3f}ms")
print(f"缓存效率: {performance_stats['cache_efficiency']:.1%}")
```

### 监督系统

自监督系统持续监控评估质量并提供自动校准：

#### 监督指标

- **准确性**: 评估结果的准确程度
- **一致性**: 评估结果的一致性
- **性能**: 评估执行的性能表现
- **缓存效率**: 缓存系统的效率
- **置信度**: 评估结果的置信水平

#### 监督配置

```python
from app.services.evaluation.evaluation_supervisor import get_evaluation_supervisor

supervisor = get_evaluation_supervisor()

# 更新监督阈值
new_thresholds = {
    "min_accuracy": 0.75,
    "min_consistency": 0.65,
    "max_evaluation_time": 25.0
}
supervisor.update_thresholds(new_thresholds)

# 获取监督报告
report = supervisor.get_supervision_report()
print(f"系统健康评分: {report['system_health']['overall_score']:.3f}")
print(f"系统状态: {report['system_health']['status']}")
```

## 批量处理

### 批量评估

```bash
# 批量评估多个任务
python -m cli.main --eval-batch --task-ids 123,124,125 --threshold 0.8 --max-iterations 3
```

### 批量配置

```python
# 批量配置评估参数
task_ids = [123, 124, 125]
for task_id in task_ids:
    default_repo.store_evaluation_config(
        task_id=task_id,
        quality_threshold=0.8,
        max_iterations=3,
        strict_mode=True
    )
```

## 最佳实践

### 1. 选择合适的评估模式

- **简单内容**: 使用基础评估或LLM智能评估
- **专业内容**: 使用多专家评估或领域专业评估
- **高质量要求**: 使用对抗性评估
- **质量监控**: 启用监督系统

### 2. 优化性能

- 启用缓存以减少重复计算
- 合理设置质量阈值避免过度迭代
- 定期清理缓存和优化系统
- 监控系统性能指标

### 3. 质量控制

- 设置合理的质量阈值（推荐0.7-0.8）
- 限制最大迭代次数（推荐3-5次）
- 启用监督系统进行质量监控
- 定期查看评估历史和统计信息

### 4. 故障排除

```bash
# 查看系统状态
python -m cli.main --eval-supervision

# 查看错误日志
python -m cli.main --eval-stats --detailed

# 重置监督状态（如果需要）
python -c "from app.services.evaluation.evaluation_supervisor import get_evaluation_supervisor; get_evaluation_supervisor().reset_supervision_state()"

# 清理缓存
python -c "from app.services.evaluation.evaluation_cache import get_evaluation_cache; get_evaluation_cache().clear_cache()"
```

## API参考

### 主要执行函数

```python
# 基础评估
execute_task_with_evaluation(task, repo, max_iterations, quality_threshold, evaluation_config, use_context, context_options)

# LLM智能评估
execute_task_with_llm_evaluation(task, repo, max_iterations, quality_threshold, evaluation_config, use_context, context_options)

# 多专家评估
execute_task_with_multi_expert_evaluation(task, repo, max_iterations, quality_threshold, selected_experts, evaluation_config, use_context, context_options)

# 对抗性评估
execute_task_with_adversarial_evaluation(task, repo, max_rounds, improvement_threshold, evaluation_config, use_context, context_options)
```

### 配置类

```python
from app.models import EvaluationConfig

config = EvaluationConfig(
    quality_threshold=0.8,           # 质量阈值
    max_iterations=3,                # 最大迭代次数
    strict_mode=True,                # 严格模式
    evaluation_dimensions=[...],     # 评估维度
    domain_specific=False,           # 领域特定评估
    custom_weights={...}             # 自定义权重
)
```

## 扩展和定制

### 添加新的评估器

1. 继承基础评估器类
2. 实现评估逻辑
3. 注册到执行系统
4. 添加CLI支持

### 自定义专家

```python
# 在expert_evaluator.py中添加新专家
new_expert = {
    "name": "custom_expert",
    "role": "自定义专家",
    "expertise": ["专业领域1", "专业领域2"],
    "evaluation_focus": ["关注点1", "关注点2"],
    "evaluation_criteria": {
        "criterion1": "标准1描述",
        "criterion2": "标准2描述"
    }
}
```

### 自定义监督指标

```python
# 在evaluation_supervisor.py中添加新指标
def _calculate_custom_metric(self, evaluation_result, execution_time):
    # 实现自定义指标计算逻辑
    return metric_value
```

## 常见问题

### Q: 评估速度太慢怎么办？
A:

1. 启用缓存系统
2. 降低质量阈值
3. 减少最大迭代次数
4. 使用更简单的评估模式

### Q: 评估结果不一致怎么办？
A:

1. 检查监督系统报告
2. 查看一致性指标
3. 考虑使用多专家评估
4. 启用严格模式

### Q: 如何提高评估准确性？
A:

1. 使用LLM智能评估
2. 启用多专家评估
3. 增加评估维度
4. 使用领域专业评估器

### Q: 系统资源占用过高怎么办？
A:

1. 定期清理缓存
2. 优化缓存配置
3. 限制并发评估数量
4. 监控系统性能指标

## 更新日志

### v2.0.0 (当前版本)

- ✅ 新增LLM智能评估器
- ✅ 新增多专家评估系统
- ✅ 新增对抗性评估机制
- ✅ 新增元认知评估系统
- ✅ 新增噬菌体专业评估器
- ✅ 新增多层缓存系统
- ✅ 新增自监督质量控制
- ✅ 完善CLI命令支持
- ✅ 新增性能优化机制

### v1.0.0

- ✅ 基础评估系统
- ✅ 评估历史管理
- ✅ 基础CLI支持

## 技术支持

如有问题或建议，请查看：

1. 系统监督报告: `python -m cli.main --eval-supervision --detailed`
2. 评估统计信息: `python -m cli.main --eval-stats --detailed`
3. 错误日志和调试信息

---

*本文档持续更新中，最后更新时间: 2024年*
