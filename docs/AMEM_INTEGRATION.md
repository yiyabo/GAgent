# A-mem集成文档

## 概述

A-mem (Agentic Memory) 是一个先进的记忆系统，已集成到Claude Code执行流程中，用于：
- 📚 记录历史执行经验
- 🔍 为新任务提供相关的历史参考
- 🧠 自动建立知识链接和演化
- 📈 持续提升任务执行成功率

## 架构

```
用户请求 → LLM分析
         ↓
    查询A-mem（获取相似任务经验）
         ↓
    增强任务描述 → Claude Code执行
         ↓
    保存结果到A-mem（异步）
```

## 配置

### 1. 环境变量

在 `.env` 文件中添加：

```bash
# A-mem配置
AMEM_ENABLED=true                    # 启用A-mem功能
AMEM_URL=http://localhost:8001       # A-mem服务地址
```

### 2. A-mem服务配置

编辑 `execute_memory/A-mem-main/config.cfg`:

```ini
[DEFAULT]
llm_backend = openai
llm_model = gpt-4o-mini
model_name = all-MiniLM-L6-v2
api_key = your-openai-api-key-here
evo_threshold = 100
```

**重要**：请将 `api_key` 替换为你的实际OpenAI API密钥。

## 启动

### 方式1：使用启动脚本

```bash
# 启动A-mem服务（端口8001）
bash scripts/start_amem.sh
```

### 方式2：手动启动

```bash
cd execute_memory/A-mem-main

# 安装依赖（首次）
pip install -e .

# 启动服务
python api.py --port 8001
```

### 验证服务

```bash
# 检查健康状态
curl http://localhost:8001/health

# 预期响应
{
  "status": "healthy",
  "memory_count": 0,
  "timestamp": "2025-01-12T00:00:00"
}
```

## 使用

### 自动集成

A-mem已自动集成到Claude Code执行流程中，无需额外操作：

1. **执行前**：系统自动查询相似任务的历史经验
2. **执行中**：Claude Code使用增强的任务描述
3. **执行后**：系统异步保存执行结果

### 示例

```python
# 用户请求
"请训练一个噬菌体-宿主互作预测模型"

# A-mem查询（自动）
→ 查找相似的历史任务
→ 返回3条最相关的经验

# 任务增强（自动）
原始任务 + 历史经验 → Claude Code

# 结果保存（自动）
执行结果 → A-mem存储 → 自动建立链接
```

### 查看记忆

```bash
# 查询记忆
curl -X POST http://localhost:8001/query_memory \
  -H "Content-Type: application/json" \
  -d '{
    "query": "训练模型",
    "top_k": 5
  }'
```

## 功能特性

### 1. 智能经验检索

- 使用ChromaDB向量搜索
- 语义相似度匹配
- 自动排序和过滤

### 2. 自动内容分析

A-mem会自动提取：
- **Keywords**: 关键术语（如"训练"、"模型"、"数据"）
- **Context**: 任务领域（如"机器学习"、"数据处理"）
- **Tags**: 分类标签（如"success"、"failure"、"claude_code"）

### 3. 记忆演化

- 自动建立相关记忆的链接
- 更新元数据和上下文
- 持续优化检索性能

### 4. 错误处理

- 所有A-mem操作都是**可选的**
- 即使A-mem失败，不影响主流程
- 异步保存，不阻塞执行

## 监控

### 日志

A-mem相关日志以 `[AMEM]` 标记：

```
[AMEM] Enhanced task with 3 historical experiences
[AMEM] Scheduled execution result save
```

### 统计

```bash
# 查看记忆数量
curl http://localhost:8001/health | jq '.memory_count'
```

## 故障排除

### A-mem服务未启动

**症状**：日志显示 `A-mem query failed`

**解决**：
```bash
# 检查服务状态
curl http://localhost:8001/health

# 如果失败，启动服务
bash scripts/start_amem.sh
```

### API密钥错误

**症状**：A-mem服务启动失败或无法分析内容

**解决**：
1. 检查 `execute_memory/A-mem-main/config.cfg`
2. 确保 `api_key` 已正确设置
3. 重启A-mem服务

### ChromaDB初始化失败

**症状**：A-mem启动时报ChromaDB错误

**解决**：
```bash
cd execute_memory/A-mem-main
rm -rf chroma_db/
python api.py --port 8001
```

## 性能影响

- **查询延迟**：~100-300ms（异步，不阻塞）
- **保存延迟**：异步执行，不影响响应时间
- **存储开销**：ChromaDB向量数据库
- **内存占用**：~200-500MB（取决于记忆数量）

## 最佳实践

1. **定期备份**：备份 `execute_memory/A-mem-main/chroma_db/`
2. **监控日志**：关注 `[AMEM]` 标记的日志
3. **调整阈值**：根据需要调整 `evo_threshold`
4. **清理旧记忆**：定期清理不相关的记忆

## 禁用A-mem

如果需要临时禁用A-mem：

```bash
# 方式1：环境变量
export AMEM_ENABLED=false

# 方式2：修改.env
AMEM_ENABLED=false
```

重启后端服务即可。

## 技术细节

### 数据流

```
Claude Code执行
    ↓
保存到A-mem
    ↓
LLM分析内容 → 提取keywords/context/tags
    ↓
ChromaDB向量化 → 存储embedding
    ↓
自动查找相似记忆 → 建立链接
```

### 记忆结构

```json
{
  "id": "uuid",
  "content": "任务描述 + 执行结果",
  "keywords": ["训练", "模型", "CNN"],
  "context": "机器学习任务",
  "tags": ["claude_code", "execution", "success"],
  "timestamp": "202501120000",
  "links": ["related-memory-id-1", "related-memory-id-2"]
}
```

## 相关文档

- [A-mem README](../execute_memory/A-mem-main/README.md)
- [A-mem API文档](../execute_memory/A-mem-main/API_README.md)
- [Claude Code文档](../tool_box/tools_impl/claude_code.py)
