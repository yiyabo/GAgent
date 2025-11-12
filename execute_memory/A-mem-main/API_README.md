# Amem Memory System FastAPI Interface

基于Amem实现的FastAPI接口，提供记忆存储和查询功能。

## 功能特性

- **添加记忆**: 存储新的记忆内容，系统会自动提取关键词、上下文和标签
- **查询记忆**: 使用语义搜索查找相关记忆
- **自动演化**: 记忆系统会自动建立记忆之间的关联关系
- **ChromaDB向量存储**: 高效的语义搜索能力

## 快速开始

### 1. 安装依赖

```bash
cd A-mem-main
pip install -r requirements.txt
```

### 2. 配置

复制配置文件模板并修改：

```bash
cp config.example.cfg ../config.cfg
```

编辑 `config.cfg` 文件，设置你的API密钥：

```ini
[DEFAULT]
llm_backend = openai
llm_model = gpt-4o-mini
model_name = all-MiniLM-L6-v2
api_key = your-openai-api-key-here
evo_threshold = 100
```

### 3. 启动服务

```bash
python api.py
```

服务将在 `http://0.0.0.0:8000` 启动。

访问 `http://localhost:8000/docs` 查看交互式API文档。

## API接口

### 1. 添加记忆 (POST /add_memory)

存储新的记忆到系统中。

**请求示例:**

```bash
curl -X POST "http://localhost:8000/add_memory" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "今天我们讨论了微服务架构以及如何处理分布式事务",
    "timestamp": "202501110900",
    "keywords": ["微服务", "分布式事务", "架构"],
    "context": "关于系统设计的技术讨论",
    "tags": ["软件工程", "架构", "微服务"]
  }'
```

**响应示例:**

```json
{
  "success": true,
  "memory_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Memory added successfully"
}
```

**参数说明:**

- `content` (必填): 记忆的内容
- `timestamp` (可选): 时间戳，格式为 YYYYMMDDHHMM
- `keywords` (可选): 关键词列表，如不提供将自动提取
- `context` (可选): 上下文信息，如不提供将自动生成
- `tags` (可选): 标签列表，如不提供将自动生成

### 2. 查询记忆 (POST /query_memory)

使用语义搜索查找相关记忆。

**请求示例:**

```bash
curl -X POST "http://localhost:8000/query_memory" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "我们讨论过哪些关于微服务的内容？",
    "top_k": 5
  }'
```

**响应示例:**

```json
{
  "success": true,
  "query": "我们讨论过哪些关于微服务的内容？",
  "count": 2,
  "results": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "content": "今天我们讨论了微服务架构以及如何处理分布式事务",
      "context": "关于系统设计的技术讨论",
      "keywords": ["微服务", "分布式事务", "架构"],
      "tags": ["软件工程", "架构", "微服务"],
      "timestamp": "202501110900",
      "score": 0.85
    }
  ]
}
```

**参数说明:**

- `query` (必填): 查询文本
- `top_k` (可选): 返回结果数量，默认5，范围1-20

### 3. 健康检查 (GET /health)

检查服务状态。

**请求示例:**

```bash
curl "http://localhost:8000/health"
```

**响应示例:**

```json
{
  "status": "healthy",
  "memory_count": 42,
  "timestamp": "2025-01-11T09:00:00"
}
```

## Python客户端示例

```python
import requests

# API基础URL
BASE_URL = "http://localhost:8000"

# 添加记忆
def add_memory(content, **kwargs):
    response = requests.post(
        f"{BASE_URL}/add_memory",
        json={
            "content": content,
            **kwargs
        }
    )
    return response.json()

# 查询记忆
def query_memory(query, top_k=5):
    response = requests.post(
        f"{BASE_URL}/query_memory",
        json={
            "query": query,
            "top_k": top_k
        }
    )
    return response.json()

# 使用示例
if __name__ == "__main__":
    # 添加记忆
    result = add_memory(
        content="学习了FastAPI的异步编程特性",
        tags=["Python", "FastAPI", "异步编程"]
    )
    print(f"Memory added: {result['memory_id']}")

    # 查询记忆
    results = query_memory("FastAPI相关的内容", top_k=3)
    print(f"Found {results['count']} memories:")
    for memory in results['results']:
        print(f"- {memory['content']}")
```

## 特性说明

### 自动内容分析

当添加记忆时，如果未提供keywords、context或tags，系统会使用LLM自动分析内容并提取：

- **Keywords**: 重要的术语和概念
- **Context**: 整体主题和领域
- **Tags**: 分类标签

### 记忆演化

系统会自动：

1. 查找相关记忆
2. 建立记忆之间的链接
3. 更新相关记忆的元数据
4. 定期整合记忆以优化检索性能

### 语义搜索

查询时系统会：

1. 使用向量相似度搜索语义相关的记忆
2. 包含通过链接关联的记忆
3. 返回按相关度排序的结果

## 配置选项

在 `config.cfg` 中可配置的选项：

- `llm_backend`: LLM后端 (openai 或 ollama)
- `llm_model`: 使用的LLM模型名称
- `model_name`: 句子嵌入模型名称
- `api_key`: LLM服务的API密钥
- `evo_threshold`: 触发记忆整合的阈值

## 注意事项

1. 首次启动时系统会初始化ChromaDB，可能需要一些时间
2. 确保配置了有效的API密钥（如使用OpenAI）
3. 记忆会持久化存储在ChromaDB中
4. 建议定期备份ChromaDB数据目录

## 故障排除

### ChromaDB初始化失败

如果遇到ChromaDB相关错误，可以尝试删除ChromaDB数据目录后重启：

```bash
rm -rf chroma_db/
python api.py
```

### API密钥错误

确保在 `config.cfg` 中正确设置了API密钥，并且密钥有效。

### 依赖安装问题

如果安装依赖时遇到问题，可以尝试：

```bash
pip install --upgrade pip
pip install -r requirements.txt --no-cache-dir
```

## 开发

### 运行测试

```bash
pytest tests/
```

### 开启调试模式

修改 `api.py` 中的日志级别：

```python
logging.basicConfig(level=logging.DEBUG)
```
