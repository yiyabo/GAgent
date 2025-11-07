# Memory-MCP 智能记忆系统

## 概述

Memory-MCP是一个集成到项目中的智能记忆管理系统，提供MCP（Model Context Protocol）兼容的记忆存储、检索和管理功能。该系统复用现有的GLM嵌入向量服务和数据库基础设施，实现高效的语义记忆搜索和智能记忆进化。

## 核心特性

### 🧠 智能记忆管理

- **自动内容分析**: 使用LLM自动提取关键词、上下文和标签
- **多类型记忆**: 支持对话、经验、知识、上下文四种记忆类型
- **重要性分级**: 从临时到关键的五级重要性管理

### 🔍 语义搜索

- **GLM嵌入向量**: 基于现有嵌入向量服务的语义相似度搜索
- **混合检索**: 语义搜索+文本搜索的双重保障
- **相似度阈值**: 可配置的最小相似度过滤

### 🔗 记忆进化

- **自动连接发现**: 基于语义相似度自动建立记忆间连接
- **定期进化**: 每10个记忆触发一次进化优化
- **关系网络**: 构建记忆知识图谱

## API 接口

### 基础端点

所有Memory-MCP接口都在 `/mcp` 路径下：

```bash
# 基础URL
http://localhost:9000/mcp
```

### 1. 保存记忆

**端点**: `POST /mcp/save_memory`

**请求格式**:
```json
{
    "content": "记忆内容",
    "memory_type": "conversation|experience|knowledge|context",
    "importance": "critical|high|medium|low|temporary",
    "tags": ["标签1", "标签2"],
    "related_task_id": 123,
    "keywords": ["关键词1", "关键词2"],
    "context": "上下文描述"
}
```

**响应格式**:
```json
{
    "context_id": "task_123_experience",
    "task_id": 123,
    "memory_type": "experience",
    "content": "记忆内容",
    "created_at": "2025-01-01T12:00:00",
    "embedding_generated": true,
    "meta": {
        "importance": "medium",
        "tags": ["标签1", "标签2"],
        "agentic_keywords": ["关键词1", "关键词2"],
        "agentic_context": "上下文描述"
    }
}
```

### 2. 查询记忆

**端点**: `POST /mcp/query_memory`

**请求格式**:
```json
{
    "search_text": "搜索内容",
    "memory_types": ["conversation", "experience"],
    "limit": 10,
    "min_similarity": 0.6
}
```

**响应格式**:
```json
{
    "memories": [
        {
            "task_id": 123,
            "memory_type": "experience",
            "content": "记忆内容",
            "similarity": 0.85,
            "created_at": "2025-01-01T12:00:00",
            "meta": {
                "importance": "medium",
                "tags": ["标签1"],
                "agentic_keywords": ["关键词"],
                "agentic_context": "上下文"
            }
        }
    ],
    "total": 1,
    "search_time_ms": 45.2
}
```

### 3. 获取统计信息

**端点**: `GET /mcp/memory/stats`

**响应格式**:
```json
{
    "total_memories": 150,
    "memory_type_distribution": {
        "conversation": 60,
        "experience": 45,
        "knowledge": 30,
        "context": 15
    },
    "importance_distribution": {
        "critical": 5,
        "high": 25,
        "medium": 80,
        "low": 35,
        "temporary": 5
    },
    "average_connections": 2.3,
    "embedding_coverage": 0.95,
    "evolution_count": 15
}
```

### 4. 自动保存任务记忆

**端点**: `POST /mcp/memory/auto_save_task`

**请求格式**:
```json
{
    "task_id": 123,
    "task_name": "任务名称",
    "content": "任务输出内容"
}
```

## 使用示例

### Python 客户端示例

```python
import requests
import json

# 基础配置
BASE_URL = "http://localhost:9000/mcp"

# 保存记忆
def save_memory(content, memory_type="experience", importance="medium"):
    response = requests.post(f"{BASE_URL}/save_memory", json={
        "content": content,
        "memory_type": memory_type,
        "importance": importance,
        "tags": ["auto_generated"]
    })
    return response.json()

# 查询记忆
def query_memory(search_text, limit=5):
    response = requests.post(f"{BASE_URL}/query_memory", json={
        "search_text": search_text,
        "limit": limit,
        "min_similarity": 0.6
    })
    return response.json()

# 使用示例
if __name__ == "__main__":
    # 保存一个经验记忆
    result = save_memory(
        "成功实现了GLM嵌入向量的批量处理优化，性能提升了3倍",
        memory_type="experience",
        importance="high"
    )
    print(f"保存成功: {result['context_id']}")
    
    # 查询相关记忆
    memories = query_memory("GLM嵌入向量优化")
    print(f"找到 {memories['total']} 条相关记忆")
    for memory in memories['memories']:
        print(f"- {memory['content'][:50]}... (相似度: {memory['similarity']:.2f})")
```

### CLI 命令示例

```bash
# 通过API保存记忆
curl -X POST http://localhost:9000/mcp/save_memory \
  -H "Content-Type: application/json" \
  -d '{
    "content": "项目重构完成，所有测试通过",
    "memory_type": "experience",
    "importance": "high",
    "tags": ["重构", "测试"]
  }'

# 查询记忆
curl -X POST http://localhost:9000/mcp/query_memory \
  -H "Content-Type: application/json" \
  -d '{
    "search_text": "重构",
    "limit": 5,
    "min_similarity": 0.7
  }'

# 获取统计信息
curl http://localhost:9000/mcp/memory/stats
```

## 数据库架构

### 记忆主表 (memories)

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,                    -- UUID记忆ID
    content TEXT NOT NULL,                  -- 记忆内容
    memory_type TEXT NOT NULL,              -- 记忆类型
    importance TEXT NOT NULL,               -- 重要性级别
    keywords TEXT,                          -- 关键词(JSON数组)
    context TEXT DEFAULT 'General',         -- 上下文
    tags TEXT,                             -- 标签(JSON数组)
    related_task_id INTEGER,               -- 关联任务ID
    links TEXT,                            -- 记忆连接(JSON数组)
    created_at TIMESTAMP,                  -- 创建时间
    last_accessed TIMESTAMP,               -- 最后访问时间
    retrieval_count INTEGER DEFAULT 0,     -- 检索次数
    evolution_history TEXT,                -- 进化历史
    embedding_generated BOOLEAN DEFAULT FALSE, -- 是否生成嵌入向量
    embedding_model TEXT                   -- 嵌入模型名称
);
```

### 嵌入向量表 (memory_embeddings)

```sql
CREATE TABLE memory_embeddings (
    memory_id TEXT PRIMARY KEY,            -- 记忆ID
    embedding_vector TEXT NOT NULL,        -- 嵌入向量(JSON)
    embedding_model TEXT DEFAULT 'embedding-2', -- 模型名称
    created_at TIMESTAMP,                  -- 创建时间
    updated_at TIMESTAMP                   -- 更新时间
);
```

## 配置选项

### 环境变量

```bash
# 记忆进化阈值（每N个记忆触发一次进化）
MEMORY_EVOLUTION_THRESHOLD=10

# 默认相似度阈值
MEMORY_DEFAULT_SIMILARITY=0.6

# 最大记忆连接数
MEMORY_MAX_CONNECTIONS=3
```

### 记忆类型说明

- **conversation**: 对话记忆，存储重要的对话内容
- **experience**: 经验记忆，存储操作经验和学习成果
- **knowledge**: 知识记忆，存储领域知识和概念
- **context**: 上下文记忆，存储环境和背景信息

### 重要性级别

- **critical**: 关键记忆，永久保存
- **high**: 高重要性，长期保存
- **medium**: 中等重要性，定期清理
- **low**: 低重要性，短期保存
- **temporary**: 临时记忆，自动清理

## 最佳实践

### 1. 记忆保存策略

```python
# 根据内容类型选择合适的记忆类型和重要性
def smart_save_memory(content, context_type="general"):
    if "错误" in content or "失败" in content:
        memory_type = "experience"
        importance = "high"
        tags = ["错误处理", "经验"]
    elif "成功" in content or "完成" in content:
        memory_type = "experience" 
        importance = "medium"
        tags = ["成功案例"]
    else:
        memory_type = "knowledge"
        importance = "medium"
        tags = ["信息"]
    
    return save_memory(content, memory_type, importance, tags)
```

### 2. 查询优化

```python
# 使用分层查询策略
def smart_query(search_text):
    # 首先高相似度精确查询
    high_quality = query_memory(search_text, min_similarity=0.8, limit=3)
    
    # 如果结果不足，降低阈值扩大搜索
    if len(high_quality['memories']) < 3:
        broader_search = query_memory(search_text, min_similarity=0.6, limit=10)
        return broader_search
    
    return high_quality
```

### 3. 记忆维护

```bash
# 定期清理临时记忆
conda run -n LLM python -c "
from app.services.memory.memory_service import get_memory_service
import asyncio
service = get_memory_service()
# 清理7天前的临时记忆
asyncio.run(service.cleanup_temporary_memories(days=7))
"

# 查看记忆统计
conda run -n LLM python -c "
from app.services.memory.memory_service import get_memory_service
import asyncio
service = get_memory_service()
stats = asyncio.run(service.get_memory_stats())
print(f'总记忆数: {stats.total_memories}')
print(f'嵌入覆盖率: {stats.embedding_coverage:.2%}')
"
```

## 故障排除

### 常见问题

**1. 嵌入向量生成失败**
```bash
# 检查嵌入服务状态
conda run -n LLM python -c "
from app.services.embeddings import get_embeddings_service
service = get_embeddings_service()
test_embedding = service.get_single_embedding('测试文本')
print('嵌入服务正常' if test_embedding else '嵌入服务异常')
"
```

**2. 记忆查询无结果**
```python
# 检查记忆数据和嵌入向量状态
def debug_memory_search(search_text):
    service = get_memory_service()
    
    # 检查总记忆数
    stats = await service.get_memory_stats()
    print(f"总记忆数: {stats.total_memories}")
    print(f"嵌入覆盖率: {stats.embedding_coverage:.2%}")
    
    # 尝试文本搜索
    text_results = await service._text_search(search_text, [], [], 5)
    print(f"文本搜索结果: {len(text_results)} 条")
    
    # 尝试语义搜索
    semantic_results = await service._semantic_search(search_text, [], [], 5, 0.3)
    print(f"语义搜索结果: {len(semantic_results)} 条")
```

**3. 记忆进化异常**
```bash
# 手动触发记忆进化
conda run -n LLM python -c "
from app.services.memory.memory_service import get_memory_service
import asyncio
service = get_memory_service()
asyncio.run(service._evolve_memories())
print('记忆进化完成')
"
```

## 集成说明

Memory-MCP系统完全集成到现有架构中：

- **复用GLM嵌入服务**: 使用 `app.services.embeddings`
- **复用数据库连接**: 使用 `app.database.get_db()`
- **复用LLM客户端**: 使用 `app.llm.get_default_client()`
- **API路由集成**: 通过FastAPI路由器集成到主应用

这确保了系统的一致性和资源的高效利用。
