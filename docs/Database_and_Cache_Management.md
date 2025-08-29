# 数据库和缓存管理系统

## 概述

本项目采用多层数据存储架构，包括主数据库、专用缓存数据库和内存缓存，以实现高性能的数据访问和智能缓存策略。

## 数据库架构

### 1. 主数据库 (`tasks.db`)

**位置**: 项目根目录下的 `tasks.db`

**核心表结构**:

#### 任务表 (`tasks`)
```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    status TEXT,
    priority INTEGER DEFAULT 100,
    parent_id INTEGER,           -- 层次结构支持
    path TEXT,                   -- 任务路径
    depth INTEGER DEFAULT 0,     -- 层次深度
    task_type TEXT DEFAULT "atomic"  -- 任务类型: root/composite/atomic
);
```

#### 任务输入输出表
```sql
-- 任务输入提示
CREATE TABLE task_inputs (
    task_id INTEGER UNIQUE,
    prompt TEXT
);

-- 任务生成内容
CREATE TABLE task_outputs (
    task_id INTEGER UNIQUE,
    content TEXT
);
```

#### 任务关系和上下文表
```sql
-- 任务间链接关系
CREATE TABLE task_links (
    from_id INTEGER,
    to_id INTEGER,
    kind TEXT,
    PRIMARY KEY (from_id, to_id, kind)
);

-- 任务上下文快照
CREATE TABLE task_contexts (
    task_id INTEGER,
    label TEXT,
    combined TEXT,
    sections TEXT,
    meta TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, label)
);
```

#### 嵌入向量表
```sql
-- GLM嵌入向量存储
CREATE TABLE task_embeddings (
    task_id INTEGER PRIMARY KEY,
    embedding_vector TEXT NOT NULL,
    embedding_model TEXT DEFAULT 'embedding-2',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
);
```

#### 评估系统表
```sql
-- 评估历史记录
CREATE TABLE evaluation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    iteration INTEGER NOT NULL,
    content TEXT NOT NULL,
    overall_score REAL NOT NULL,
    dimension_scores TEXT NOT NULL,
    suggestions TEXT,
    needs_revision BOOLEAN NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

-- 评估配置
CREATE TABLE evaluation_configs (
    task_id INTEGER PRIMARY KEY,
    quality_threshold REAL DEFAULT 0.8,
    max_iterations INTEGER DEFAULT 3,
    evaluation_dimensions TEXT,
    domain_specific BOOLEAN DEFAULT FALSE,
    strict_mode BOOLEAN DEFAULT FALSE,
    custom_weights TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
);
```

### 2. 嵌入缓存数据库 (`embedding_cache.db`)

**位置**: 项目根目录下的 `embedding_cache.db`

**用途**: 缓存文本的嵌入向量，避免重复计算

**表结构**:
```sql
CREATE TABLE embedding_cache (
    text_hash TEXT PRIMARY KEY,
    embedding_json TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at REAL NOT NULL,
    access_count INTEGER DEFAULT 1,
    last_accessed REAL NOT NULL
);
```

**特性**:
- 基于文本哈希的快速查找
- 支持多模型嵌入缓存
- 访问计数和时间戳跟踪
- 自动LRU清理机制

### 3. 评估缓存数据库 (`evaluation_cache.db`)

**位置**: 项目根目录下的 `evaluation_cache.db`

**用途**: 缓存评估结果，提高评估性能

**表结构**:
```sql
CREATE TABLE evaluation_cache (
    cache_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    task_context_hash TEXT NOT NULL,
    evaluation_method TEXT NOT NULL,
    evaluation_result TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 1,
    cache_metadata TEXT
);
```

**索引优化**:
```sql
CREATE INDEX idx_content_hash ON evaluation_cache(content_hash);
CREATE INDEX idx_method_hash ON evaluation_cache(evaluation_method, content_hash);
CREATE INDEX idx_last_accessed ON evaluation_cache(last_accessed);
-- 核心业务相关索引（与 app/database.py 一致）
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status_prio_id ON tasks(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_tasks_priority_id ON tasks(priority, id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent_id ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_path ON tasks(path);
CREATE INDEX IF NOT EXISTS idx_tasks_depth ON tasks(depth);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_task_links_to_kind ON task_links(to_id, kind);
CREATE INDEX IF NOT EXISTS idx_task_links_from_kind ON task_links(from_id, kind);
CREATE INDEX IF NOT EXISTS idx_task_contexts_task_id ON task_contexts(task_id);
CREATE INDEX IF NOT EXISTS idx_task_contexts_created_at ON task_contexts(created_at);
```

## 缓存系统架构

### 1. 多层缓存策略

#### 嵌入缓存 (`EmbeddingCache`)
- **内存缓存**: 快速访问最近使用的嵌入向量
- **持久化缓存**: SQLite数据库存储，支持跨会话缓存
- **LRU淘汰**: 内存缓存满时自动清理最少使用的条目
- **批量处理**: 支持批量获取和存储，提高效率

**配置参数**:
```python
# 环境变量配置
EMBEDDING_CACHE_SIZE=10000        # 内存缓存大小
EMBEDDING_CACHE_PERSISTENT=1      # 启用持久化缓存
```

#### 评估缓存 (`EvaluationCache`)
- **双层架构**: 内存 + SQLite持久化存储
- **智能键生成**: 基于内容、上下文、方法和配置的复合哈希
- **时效性控制**: 支持缓存过期时间设置
- **自动优化**: 定期清理过期和低频访问条目

**缓存键策略**:
```python
cache_key = sha256(f"{evaluation_method}:{content_hash}:{context_hash}:{config_hash}")
```

### 2. 缓存管理功能

#### 性能统计
```python
# 获取缓存统计信息
cache_stats = get_cache_stats()
# 返回: hit_rate, total_requests, memory_size, persistent_size等
```

#### 缓存清理
```python
# 清理特定方法的缓存
clear_evaluation_cache("llm_intelligent")

# 清理所有缓存
clear_evaluation_cache()

# 清理过期条目
cache.cleanup_old_entries(days=30)

# 重置数据库（清空所有数据）
python -c "
from cli.commands.database_commands import DatabaseCommands
db_cmd = DatabaseCommands()
result = db_cmd.reset_database()
print(f'重置完成: {result}')
"
```

#### 缓存优化
```python
# 自动优化缓存
optimization_result = cache.optimize_cache()
# 清理7天前的条目、低频访问条目，执行数据库VACUUM
```

## 数据库连接管理

### 1. 连接池和上下文管理

```python
# 主数据库连接
from app.database import get_db

with get_db() as conn:
    cursor = conn.execute("SELECT * FROM tasks")
    results = cursor.fetchall()
```

### 2. 线程安全

- 所有缓存操作使用 `threading.RLock()` 保证线程安全
- SQLite连接采用上下文管理器自动关闭
- 支持并发读写操作

## 配置管理

### 1. 环境变量配置

```bash
# 数据库路径
DB_PATH=tasks.db

# 嵌入缓存配置
EMBEDDING_CACHE_SIZE=10000
EMBEDDING_CACHE_PERSISTENT=1

# GLM API配置
GLM_API_KEY=your_api_key
GLM_EMBEDDING_MODEL=embedding-2
GLM_BATCH_SIZE=25
```

### 2. 配置类

```python
from app.services.config import get_config

config = get_config()
# 自动从环境变量加载并验证配置
```

## 性能优化策略

### 1. 索引优化

- 为所有常用查询字段创建索引
- 复合索引支持多条件查询
- 定期分析查询性能并优化索引

### 2. 缓存策略

- **预热缓存**: 系统启动时加载常用数据
- **批量操作**: 减少数据库往返次数
- **异步处理**: 非关键路径的缓存更新异步执行

### 3. 内存管理

- 内存缓存大小限制，防止内存溢出
- LRU淘汰算法，保留热点数据
- 定期清理过期和无效缓存

## 监控和维护

### 1. 缓存监控

```python
# 实时监控缓存性能
python -m cli.main --eval-supervision --detailed
```

**监控指标**:
- 缓存命中率
- 内存使用情况
- 数据库大小
- 查询响应时间

### 2. 维护操作

```bash
# 数据库优化
conda run -n LLM python -c "
from app.services.evaluation_cache import get_evaluation_cache
cache = get_evaluation_cache()
result = cache.optimize_cache()
print(f'优化完成: {result}')
"
```

### 3. 备份策略

- 定期备份主数据库文件
- 缓存数据库可重建，无需备份

## 故障排除

### 常见问题

**缓存性能下降**:
```bash
# 检查缓存命中率
conda run -n LLM python -c "from app.services.evaluation_cache import get_cache_stats; print(get_cache_stats())"

# 执行缓存优化
conda run -n LLM python -c "from app.services.evaluation_cache import get_evaluation_cache; get_evaluation_cache().optimize_cache()"
```

**磁盘空间不足**:
```bash
# 清理旧缓存条目
conda run -n LLM python -c "from app.services.evaluation_cache import get_evaluation_cache; get_evaluation_cache().cleanup_old_entries(days=7)"
```

### 调试工具

```bash
# 查看数据库结构
sqlite3 tasks.db ".schema"

# 检查缓存统计
conda run -n LLM python -c "from app.services.evaluation_cache import get_cache_stats; print(get_cache_stats())"

# 清理所有缓存
conda run -n LLM python -c "from app.services.evaluation_cache import clear_evaluation_cache; clear_evaluation_cache()"
```

## 最佳实践

- 使用上下文管理器管理数据库连接
- 批量操作优于单条操作
- 定期监控缓存性能
- 配置适当的缓存大小

## 总结

本项目的数据库和缓存系统提供了完整的数据持久化、高效的缓存机制和智能的管理策略，确保了系统的高性能、可扩展性和可维护性。