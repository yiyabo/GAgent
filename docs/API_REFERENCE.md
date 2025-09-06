# AI-Driven 智能任务编排系统 API 参考

## 🚀 递归任务分解 API

### 任务分解端点

#### POST /tasks/{task_id}/decompose
对指定任务进行智能分解。

**请求参数:**
```json
{
  "max_subtasks": 5,      // 最大子任务数量 (2-20，默认8)
  "force": false          // 强制分解，忽略现有子任务
}
```

**响应示例:**
```json
{
  "success": true,
  "task_id": 123,
  "subtasks": [
    {
      "id": 124,
      "name": "用户注册模块",
      "type": "composite",
      "priority": 100
    }
  ],
  "decomposition_depth": 1
}
```

#### POST /tasks/{task_id}/decompose/with-evaluation
带质量评估的任务分解，支持迭代改进。

**请求参数:**
```json
{
  "max_subtasks": 5,
  "quality_threshold": 0.7,    // 质量阈值 (0.0-1.0)
  "max_iterations": 2          // 最大迭代次数
}
```

**响应示例:**
```json
{
  "success": true,
  "task_id": 123,
  "subtasks": [...],
  "quality_evaluation": {
    "quality_score": 0.85,
    "needs_refinement": false,
    "issues": [],
    "suggestions": []
  },
  "best_quality_score": 0.85,
  "meets_threshold": true,
  "iterations_performed": 1
}
```

#### GET /tasks/{task_id}/complexity
评估任务复杂度。

**响应示例:**
```json
{
  "task_id": 123,
  "complexity": "high",           // high/medium/low
  "task_type": "root",           // root/composite/atomic
  "should_decompose": true,
  "depth": 0,
  "existing_children": 0
}
```

#### GET /tasks/{task_id}/decomposition/recommendation
获取任务分解建议。

**请求参数:**
- `min_complexity_score`: 最小复杂度分数 (默认0.6)

**响应示例:**
```json
{
  "task_id": 123,
  "recommendation": {
    "should_decompose": true,
    "complexity": "high",
    "complexity_score": 0.9,
    "recommendations": [
      "任务复杂度较高，建议进行分解",
      "建议分解为4-6个子任务"
    ]
  },
  "analysis": {
    "basic_decomposition_eligible": true,
    "complexity_sufficient": true,
    "within_depth_limit": true,
    "not_atomic": true
  },
  "timestamp": "2024-08-31T10:30:00Z"
}
```

#### POST /plans/{title}/decompose
递归分解整个计划中的所有任务。

**请求参数:**
```json
{
  "max_depth": 3    // 最大分解深度
}
```

**响应示例:**
```json
{
  "success": true,
  "plan_title": "智能系统开发计划",
  "decompositions": [...],
  "total_tasks_decomposed": 5
}
```

### 任务分解算法说明

#### 复杂度评估算法
基于关键词密度和任务描述长度进行智能评估：

**高复杂度关键词:**
- 系统、架构、平台、框架、完整、全面、端到端、整体、综合

**中等复杂度关键词:**
- 模块、组件、功能、特性、集成、优化、重构、扩展

**低复杂度关键词:**
- 修复、调试、测试、文档、配置、部署、更新、检查

#### 任务类型体系
```
ROOT (深度0)     → COMPOSITE (深度1)  → ATOMIC (深度2)
高复杂度项目      → 中等粒度任务        → 可执行最小单元
```

#### 质量评估指标
- **子任务数量**: 2-8个为最优
- **名称质量**: 避免空名称和泛化名称
- **类型一致性**: 同层级任务类型应保持一致
- **重叠检测**: 避免子任务间功能重叠

## 🎯 评估系统 API

### 核心执行函数

### execute_task_with_evaluation()

基础评估执行函数。

```python
def execute_task_with_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult
```

**参数:**
- `task`: 任务对象或字典
- `repo`: 任务仓库实例
- `max_iterations`: 最大迭代次数
- `quality_threshold`: 质量阈值 (0.0-1.0)
- `evaluation_config`: 评估配置对象
- `use_context`: 是否使用上下文
- `context_options`: 上下文选项

**返回:** `TaskExecutionResult` 对象

### execute_task_with_llm_evaluation()

LLM智能评估执行函数。

```python
def execute_task_with_llm_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult
```

**特点:**
- 使用大语言模型进行深度语义理解
- 提供6个维度的详细评估
- 智能生成改进建议

### execute_task_with_multi_expert_evaluation()

多专家评估执行函数。

```python
def execute_task_with_multi_expert_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    selected_experts: Optional[List[str]] = None,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult
```

**参数:**
- `selected_experts`: 选择的专家列表，可选值:
  - `"theoretical_biologist"`: 理论生物学家
  - `"clinical_physician"`: 临床医师
  - `"regulatory_expert"`: 监管专家
  - `"research_scientist"`: 研究科学家
  - `"biotech_entrepreneur"`: 生物技术企业家

### execute_task_with_adversarial_evaluation()

对抗性评估执行函数。

```python
def execute_task_with_adversarial_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_rounds: int = 3,
    improvement_threshold: float = 0.1,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult
```

**参数:**
- `max_rounds`: 最大对抗轮数
- `improvement_threshold`: 改进阈值

## 配置类

### EvaluationConfig

评估配置类（Pydantic）。

```python
class EvaluationConfig(BaseModel):
    quality_threshold: float = 0.8
    max_iterations: int = 3
    evaluation_dimensions: List[str] = [
        "relevance", "completeness", "accuracy", "clarity", "coherence"
    ]
    domain_specific: bool = False
    strict_mode: bool = False
    custom_weights: Optional[Dict[str, float]] = None
```

**字段说明:**
- `quality_threshold`: 质量阈值 (0.0-1.0)
- `max_iterations`: 最大迭代次数
- `strict_mode`: 严格模式，启用更严格的评估标准
- `evaluation_dimensions`: 评估维度列表
- `domain_specific`: 是否启用领域特定评估
- `custom_weights`: 自定义维度权重

**评估维度选项:**
- `"relevance"`: 相关性
- `"completeness"`: 完整性
- `"accuracy"`: 准确性
- `"clarity"`: 清晰度
- `"coherence"`: 连贯性
- `"scientific_rigor"`: 科学严谨性

### TaskExecutionResult

任务执行结果类（Pydantic）。

```python
class TaskExecutionResult(BaseModel):
    task_id: int
    status: str
    content: Optional[str] = None
    evaluation: Optional[EvaluationResult] = None
    iterations: int = 1
    execution_time: Optional[float] = None
```

**字段说明:**
- `task_id`: 任务ID
- `status`: 执行状态 ("done", "needs_review", "failed")
- `content`: 生成的内容
- `evaluation`: 评估结果
- `iterations`: 完成的迭代次数
- `execution_time`: 执行时间(秒)
- `metadata`: 额外元数据

### EvaluationResult

评估结果类（Pydantic）。

```python
class EvaluationResult(BaseModel):
    overall_score: float
    dimensions: EvaluationDimensions
    suggestions: List[str] = []
    needs_revision: bool = False
    iteration: int = 0
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
```

## 评估器类

### LLMEvaluator

LLM智能评估器。

```python
class LLMEvaluator:
    def __init__(self, config: Optional[EvaluationConfig] = None)
    
    def evaluate_content_intelligent(
        self, 
        content: str, 
        task_context: Dict[str, Any], 
        iteration: int = 1
    ) -> EvaluationResult
```

**使用示例:**
```python
from app.services.evaluation.llm_evaluator import get_llm_evaluator

evaluator = get_llm_evaluator()
result = evaluator.evaluate_content_intelligent(
    content="待评估内容",
    task_context={"name": "任务名称", "task_type": "content_generation"},
    iteration=1
)
```

## Benchmark 基准评测接口

### REST API

```http
POST /benchmark
Content-Type: application/json

{
  "topic": "抗菌素耐药",
  "configs": [
    "base,use_context=False",
    "ctx,use_context=True,max_chars=3000,semantic_k=5"
  ],
  "sections": 5
}
```

返回：
- `summary_md`: 汇总 Markdown 表
- `metrics`: 每个配置的均值、维度均值、失败数、计数等
- `files`: 每个配置生成的 MD 文件路径（若设置 outdir）
- `csv_path`: 统一 CSV 路径（若设置 csv_path）

### CLI

```bash
python -m cli.main --benchmark \
  --benchmark-topic "抗菌素耐药" \
  --benchmark-configs "base,use_context=False" "ctx,use_context=True,max_chars=3000,semantic_k=5" \
  --benchmark-sections 5 \
  --benchmark-outdir results/抗菌素耐药 \
  --benchmark-csv results/抗菌素耐药/summary.csv \
  --benchmark-output results/抗菌素耐药/overview.md
```

### MultiExpertEvaluator

多专家评估器。

```python
class MultiExpertEvaluator:
    def __init__(self, config: Optional[EvaluationConfig] = None)
    
    def evaluate_with_multiple_experts(
        self,
        content: str,
        task_context: Dict[str, Any],
        selected_experts: Optional[List[str]] = None,
        iteration: int = 1
    ) -> Dict[str, Any]
```

**返回结果结构:**
```python
{
    "expert_evaluations": {
        "expert_name": {
            "overall_score": float,
            "expert_role": str,
            "confidence_level": float,
            "major_concerns": List[str],
            "specific_suggestions": List[str]
        }
    },
    "consensus": {
        "overall_score": float,
        "consensus_confidence": float,
        "specific_suggestions": List[str]
    },
    "disagreements": [
        {
            "field": str,
            "disagreement_level": float,
            "lowest_scorer": str,
            "highest_scorer": str
        }
    ],
    "metadata": Dict[str, Any]
}
```

### AdversarialEvaluator

对抗性评估器。

```python
class AdversarialEvaluator:
    def __init__(self, config: Optional[EvaluationConfig] = None)
    
    def adversarial_evaluate(
        self,
        content: str,
        task_context: Dict[str, Any],
        max_rounds: int = 3,
        improvement_threshold: float = 0.1
    ) -> Dict[str, Any]
```

### MetaEvaluator

元认知评估器。

```python
class MetaEvaluator:
    def __init__(self, config: Optional[EvaluationConfig] = None)
    
    def meta_evaluate_assessment_quality(
        self,
        evaluation_history: List[Dict[str, Any]],
        task_context: Dict[str, Any],
        current_evaluation: Dict[str, Any]
    ) -> Dict[str, Any]
```

### PhageEvaluator

噬菌体专业评估器。

```python
class PhageEvaluator:
    def __init__(self, config: Optional[EvaluationConfig] = None)
    
    def evaluate_phage_content(
        self,
        content: str,
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]
```

## 缓存系统

### EvaluationCache

评估缓存管理器。

```python
class EvaluationCache:
    def get_cache_stats(self) -> Dict[str, Any]
    def optimize_cache(self) -> Dict[str, Any]
    def clear_cache(self) -> bool
    def get_performance_stats(self) -> Dict[str, Any]
```

**使用示例:**
```python
from app.services.evaluation.evaluation_cache import get_evaluation_cache

cache = get_evaluation_cache()

# 获取缓存统计
stats = cache.get_cache_stats()
print(f"缓存命中率: {stats['hit_rate']:.1%}")

# 优化缓存
optimization_result = cache.optimize_cache()
print(f"清理了 {optimization_result['entries_removed']} 个条目")
```

## 监督系统

### EvaluationSupervisor

评估质量监督器。

```python
class EvaluationSupervisor:
    def monitor_evaluation(
        self, 
        evaluation_result: EvaluationResult,
        evaluation_method: str,
        execution_time: float,
        content: str,
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]
    
    def get_supervision_report(self) -> Dict[str, Any]
    
    def update_thresholds(self, new_thresholds: Dict[str, float]) -> bool
    
    def reset_supervision_state(self) -> bool
```

**使用示例:**
```python
from app.services.evaluation.evaluation_supervisor import get_evaluation_supervisor

supervisor = get_evaluation_supervisor()

# 获取监督报告
report = supervisor.get_supervision_report()
print(f"系统健康评分: {report['system_health']['overall_score']:.3f}")

# 更新监督阈值
new_thresholds = {
    "min_accuracy": 0.75,
    "max_evaluation_time": 30.0
}
supervisor.update_thresholds(new_thresholds)
```

## 工具函数

### monitor_evaluation()

监控单次评估的便捷函数。

```python
def monitor_evaluation(
    evaluation_result: EvaluationResult,
    evaluation_method: str,
    execution_time: float,
    content: str,
    task_context: Dict[str, Any]
) -> Dict[str, Any]
```

### get_supervision_report()

获取监督报告的便捷函数。

```python
def get_supervision_report() -> Dict[str, Any]
```

## 错误处理

### 常见异常

```python
# 评估配置错误
class EvaluationConfigError(Exception):
    pass

# 评估执行错误
class EvaluationExecutionError(Exception):
    pass

# 缓存错误
class CacheError(Exception):
    pass

# 监督系统错误
class SupervisionError(Exception):
    pass
```

### 错误处理示例

```python
try:
    result = execute_task_with_llm_evaluation(
        task=task,
        quality_threshold=0.8,
        max_iterations=3
    )
except EvaluationExecutionError as e:
    print(f"评估执行失败: {e}")
    # 处理错误
except Exception as e:
    print(f"未知错误: {e}")
    # 通用错误处理
```

## 最佳实践

### 1. 配置优化

```python
# 推荐的配置
config = EvaluationConfig(
    quality_threshold=0.8,  # 适中的质量要求
    max_iterations=3,       # 避免过度迭代
    strict_mode=True,       # 启用严格模式
    evaluation_dimensions=[
        "relevance", "completeness", "accuracy", 
        "clarity", "coherence", "scientific_rigor"
    ]
)
```

### 2. 错误恢复

```python
def robust_evaluation(task, max_retries=3):
    """带重试机制的评估"""
    for attempt in range(max_retries):
        try:
            return execute_task_with_llm_evaluation(task)
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            time.sleep(2 ** attempt)  # 指数退避
```

### 3. 性能监控

```python
def monitored_evaluation(task):
    """带性能监控的评估"""
    start_time = time.time()
    
    try:
        result = execute_task_with_llm_evaluation(task)
        
        # 记录性能指标
        execution_time = time.time() - start_time
        if execution_time > 30:  # 超过30秒警告
            print(f"⚠️ 评估耗时较长: {execution_time:.2f}秒")
        
        return result
        
    except Exception as e:
        print(f"❌ 评估失败: {e}")
        raise
```

### 4. 批量处理

```python
def batch_evaluation(tasks, batch_size=5):
    """批量评估处理"""
    results = []
    
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        
        for task in batch:
            try:
                result = execute_task_with_llm_evaluation(task)
                results.append(result)
            except Exception as e:
                print(f"任务 {task.get('id', 'unknown')} 评估失败: {e}")
                results.append(None)
        
        # 批次间暂停，避免过载
        if i + batch_size < len(tasks):
            time.sleep(1)
    
    return results
```

## 版本兼容性

### 当前版本: 2.0.0

**新增功能:**
- LLM智能评估
- 多专家评估系统
- 对抗性评估机制
- 元认知评估
- 噬菌体专业评估
- 自监督质量控制
- 多层缓存系统

**向后兼容:**
- 保持与1.x版本的API兼容
- 旧的评估函数仍然可用
- 配置参数向后兼容

### 迁移指南

从1.x版本迁移到2.0版本:

```python
# 旧版本 (1.x)
result = execute_task(task, enable_evaluation=True)

# 新版本 (2.0) - 推荐
result = execute_task_with_llm_evaluation(task)

# 或者保持兼容
result = execute_task(task, enable_evaluation=True)  # 仍然有效
```

## 扩展开发

### 自定义评估器

```python
from app.services.evaluation.content_evaluator import ContentEvaluator

class CustomEvaluator(ContentEvaluator):
    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)
    
    def evaluate_content(
        self, 
        content: str, 
        task_context: Dict[str, Any], 
        iteration: int = 1
    ) -> EvaluationResult:
        # 实现自定义评估逻辑
        pass
```

### 自定义专家

```python
# 在expert_evaluator.py中添加
CUSTOM_EXPERT = {
    "name": "custom_expert",
    "role": "自定义专家",
    "expertise": ["专业领域"],
    "evaluation_focus": ["关注点"],
    "evaluation_criteria": {
        "criterion1": "评估标准1"
    }
}
```

---

*API参考文档 v2.0.0 - 最后更新: 2024年*
