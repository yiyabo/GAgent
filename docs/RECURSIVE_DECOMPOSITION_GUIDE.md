# 递归任务分解系统指南

## 🎯 概述

递归任务分解系统是AI-Driven智能任务编排系统的核心功能之一，能够将复杂的高层任务智能地分解为可执行的子任务。系统采用三级分解架构（ROOT → COMPOSITE → ATOMIC），结合智能复杂度评估和质量控制机制，确保分解结果的合理性和可执行性。

## 🏗️ 系统架构

### 三级分解体系

```
ROOT任务 (深度0)
├── 高复杂度项目或系统级任务
├── 需要分解为主要功能模块
└── 例：构建完整的电商系统

COMPOSITE任务 (深度1) 
├── 中等复杂度的功能模块
├── 需要进一步分解为具体步骤
└── 例：用户管理模块、商品管理模块

ATOMIC任务 (深度2)
├── 低复杂度的具体执行单元
├── 可以直接执行的最小任务
└── 例：实现用户注册接口、设计数据库表
```

### 核心组件

1. **复杂度评估器** (`evaluate_task_complexity`)
   - 基于关键词密度分析
   - 考虑任务描述长度
   - 输出: high/medium/low

2. **任务类型判断** (`determine_task_type`) 
   - 根据复杂度和深度确定类型
   - 支持类型强制指定
   - 输出: ROOT/COMPOSITE/ATOMIC

3. **分解决策器** (`should_decompose_task`)
   - 综合考虑任务类型、深度、现有子任务
   - 防止过度分解和重复分解
   - 输出: True/False

4. **递归分解器** (`decompose_task`)
   - 调用LLM服务生成分解方案
   - 创建子任务并设置层级关系
   - 返回完整分解结果

5. **质量评估器** (`evaluate_decomposition_quality`)
   - 评估分解质量的多个维度
   - 提供改进建议和问题诊断
   - 支持迭代优化

## 🚀 快速开始

### 基础使用

```bash
# 启动API服务
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 创建一个根任务
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "开发智能客服系统", 
    "task_type": "root"
  }'

# 分解任务 (假设任务ID为123)
curl -X POST http://localhost:8000/tasks/123/decompose \
  -H "Content-Type: application/json" \
  -d '{
    "max_subtasks": 5,
    "force": false
  }'
```

### 高级功能

```bash
# 带质量评估的分解
curl -X POST http://localhost:8000/tasks/123/decompose/with-evaluation \
  -H "Content-Type: application/json" \
  -d '{
    "max_subtasks": 6,
    "quality_threshold": 0.8,
    "max_iterations": 3
  }'

# 获取分解建议
curl -X GET "http://localhost:8000/tasks/123/decomposition/recommendation?min_complexity_score=0.6"

# 评估任务复杂度
curl -X GET http://localhost:8000/tasks/123/complexity
```

## 📋 配置参数

### 全局配置

```python
# 递归分解配置常量 (app/services/planning/recursive_decomposition.py)
MAX_DECOMPOSITION_DEPTH = 3    # 最大分解深度
MIN_ATOMIC_TASKS = 2           # 最小子任务数
MAX_ATOMIC_TASKS = 8           # 最大子任务数
```

### API参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_subtasks` | int | 8 | 最大子任务数量 (2-20) |
| `force` | bool | false | 强制分解，忽略现有子任务 |
| `quality_threshold` | float | 0.7 | 质量阈值 (0.0-1.0) |
| `max_iterations` | int | 2 | 最大迭代次数 (1-5) |
| `min_complexity_score` | float | 0.6 | 最小复杂度分数 |

## 🎨 使用场景

### 场景1：项目管理

```python
# 大型软件项目分解
root_task = "开发企业级CRM系统"
# 自动分解为：
# ├── 用户认证模块 (COMPOSITE)
# ├── 客户管理模块 (COMPOSITE) 
# ├── 销售管理模块 (COMPOSITE)
# └── 报表分析模块 (COMPOSITE)
```

### 场景2：学术研究

```python
# 研究项目分解
root_task = "人工智能在医疗诊断中的应用研究"
# 自动分解为：
# ├── 文献调研与综述 (COMPOSITE)
# ├── 数据集构建与预处理 (COMPOSITE)
# ├── 模型设计与实现 (COMPOSITE)
# └── 实验验证与分析 (COMPOSITE)
```

### 场景3：产品开发

```python
# 产品功能分解
root_task = "智能家居控制APP"
# 自动分解为：
# ├── 设备连接管理 (COMPOSITE)
# ├── 用户界面设计 (COMPOSITE)
# ├── 自动化场景配置 (COMPOSITE)
# └── 数据可视化展示 (COMPOSITE)
```

## 🔧 高级特性

### 质量驱动分解

系统内置质量评估机制，确保分解结果符合以下标准：

```python
# 质量评估维度
quality_metrics = {
    "subtask_count": "2-8个子任务为最优",
    "naming_quality": "避免空名称和泛化名称",
    "type_consistency": "同层级任务类型保持一致", 
    "overlap_detection": "避免子任务间功能重叠",
    "coverage_completeness": "子任务应覆盖父任务全部功能"
}
```

### 迭代改进机制

```python
# 带评估的分解会自动进行迭代改进
result = decompose_task_with_evaluation(
    task_id=123,
    quality_threshold=0.8,  # 质量阈值
    max_iterations=3        # 最大迭代次数
)

# 如果首次分解质量低于阈值，系统会：
# 1. 分析质量问题
# 2. 生成改进建议  
# 3. 重新执行分解
# 4. 比较质量分数
# 5. 返回最佳结果
```

### 深度控制

```python
# 防止过度分解的深度控制
def should_decompose_task(task, repo):
    depth = task.get("depth", 0)
    
    # 深度限制检查
    if depth >= MAX_DECOMPOSITION_DEPTH - 1:  # depth=2时停止
        return False
    
    # 任务类型检查  
    if determine_task_type(task) == TaskType.ATOMIC:
        return False
        
    # 现有子任务检查
    children = repo.get_children(task["id"])
    if len(children) >= MIN_ATOMIC_TASKS:
        return False
        
    return True
```

## 📊 质量指标

### 分解质量评分

```python
# 质量评分算法
def calculate_quality_score(parent_task, subtasks):
    score = 1.0  # 满分
    
    # 子任务数量检查
    if len(subtasks) < 2:
        score -= 0.3  # 分解不充分
    elif len(subtasks) > 8:
        score -= 0.2  # 分解过细
    
    # 命名质量检查
    poor_names = [s for s in subtasks if not s["name"] or "子任务" in s["name"]]
    score -= 0.1 * len(poor_names)
    
    # 类型一致性检查
    expected_type = get_expected_child_type(parent_task)
    inconsistent = [s for s in subtasks if s["type"] != expected_type]
    if inconsistent:
        score -= 0.15
    
    # 功能重叠检查
    if has_functional_overlap(subtasks):
        score -= 0.1
        
    return max(0.0, min(1.0, score))
```

### 性能指标

- **分解成功率**: >95% (对于符合分解条件的任务)
- **平均响应时间**: 3-8秒 (取决于LLM服务性能)
- **质量分数**: >0.8 (高质量分解的平均分数)
- **深度控制准确性**: 100% (严格防止超深度分解)

## 🚨 错误处理

### 常见错误类型

1. **任务不存在**
```json
{
  "success": false,
  "error": "Task not found",
  "error_code": 1002
}
```

2. **不需要分解**  
```json
{
  "success": false,
  "error": "Task does not need decomposition",
  "error_code": 1001
}
```

3. **超出深度限制**
```json
{
  "success": false, 
  "error": "Maximum decomposition depth exceeded",
  "error_code": 1001
}
```

4. **LLM服务失败**
```json
{
  "success": false,
  "error": "Failed to generate subtasks",
  "error_code": 3001
}
```

### 处理建议

```python
# 错误处理最佳实践
def handle_decomposition_error(error_response):
    error_code = error_response.get("error_code")
    
    if error_code == 1002:  # 任务不存在
        return "请检查任务ID是否正确"
    elif error_code == 1001:  # 业务逻辑错误
        return "任务可能已经分解过或不符合分解条件"
    elif error_code == 3001:  # 系统错误
        return "请稍后重试或联系管理员"
    else:
        return "未知错误，请查看详细错误信息"
```

## 🔍 调试与监控

### 调试模式

```bash
# 启用调试模式
export DECOMP_DEBUG=1
# 或
export CONTEXT_DEBUG=1

# 查看调试日志
tail -f logs/decomposition.log
```

### 监控指标

```python
# 关键监控指标
monitoring_metrics = {
    "decomposition_success_rate": "分解成功率",
    "average_response_time": "平均响应时间", 
    "quality_score_distribution": "质量分数分布",
    "depth_control_accuracy": "深度控制准确性",
    "llm_service_availability": "LLM服务可用性"
}
```

## 🛠️ 扩展开发

### 自定义复杂度评估

```python
# 扩展复杂度关键词
CUSTOM_KEYWORDS = {
    "domain_specific": ["生信", "基因", "蛋白质", "分子"],
    "technical_level": ["算法", "模型", "训练", "推理"] 
}

def custom_evaluate_complexity(task_name, task_prompt):
    # 自定义复杂度评估逻辑
    pass
```

### 自定义质量评估

```python  
def custom_quality_evaluator(parent_task, subtasks):
    # 自定义质量评估逻辑
    custom_score = evaluate_custom_metrics(parent_task, subtasks)
    return {
        "quality_score": custom_score,
        "custom_metrics": {...}
    }
```

## 📚 参考资料

- [API参考文档](API_REFERENCE.md#递归任务分解-api)
- [系统架构说明](README.md#系统架构)
> 说明：相关测试样例已重构为端到端流程测试，可通过 Quick Start 中的 REST/CLI 命令复现与验证。

---

**最后更新**: 2025年8月31日  
**版本**: v2.0.0
