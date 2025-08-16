# 智能上下文感知的LLM任务编排系统

*English version: [README.md](./README.md)*

一个生产级的AI任务编排系统，将高层目标转化为可执行的计划，具备智能上下文感知、依赖管理和预算控制功能。

## 🚀 核心特性

- **智能计划生成**：从高层目标自动生成可执行的任务计划
- **上下文智能**：多源上下文汇集（依赖图、TF-IDF检索、全局索引）
- **依赖感知**：基于DAG的调度，支持循环检测
- **预算管理**：Token/字符限制，智能内容摘要
- **可重现执行**：上下文快照和确定性排序
- **生产就绪**：FastAPI后端，全面测试，支持开发模式

## 📋 快速开始

### 环境准备

```bash
# 安装依赖
conda run -n LLM python -m pip install -r requirements.txt

# 设置环境变量
export GLM_API_KEY=your_key_here
# 或使用开发模式
# export LLM_MOCK=1
```

### 启动服务器

```bash
conda run -n LLM python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 示例工作流

```bash
# 1. 生成计划
curl -X POST http://127.0.0.1:8000/plans/propose \
  -H "Content-Type: application/json" \
  -d '{"goal": "编写基因编辑技术白皮书"}'

# 2. 批准计划（可先编辑）
curl -X POST http://127.0.0.1:8000/plans/approve \
  -H "Content-Type: application/json" \
  --data-binary @plan.json

# 3. 执行任务（带上下文感知）
curl -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "title": "基因编辑技术白皮书",
    "schedule": "dag",
    "use_context": true,
    "context_options": {
      "include_deps": true,
      "tfidf_k": 2,
      "max_chars": 1200,
      "save_snapshot": true
    }
  }'

# 4. 获取最终汇总输出
curl http://127.0.0.1:8000/plans/基因编辑技术白皮书/assembled
```

## 🧠 系统原理

### 系统架构

系统遵循**计划 → 审核 → 执行**工作流，具备智能上下文编排：

```text
目标输入 → 计划生成 → 人工审核 → 计划批准 → 任务调度 → 上下文汇集 → 预算控制 → LLM执行 → 结果汇总
```

### 核心工作流程

1. **计划生成** (`/plans/propose`)
   - LLM分析用户目标，生成结构化任务分解
   - 返回JSON格式计划，包含任务、优先级和初始提示词
   - 无数据持久化 - 允许人工审核和编辑

2. **计划批准** (`/plans/approve`)
   - 将批准的计划持久化到数据库
   - 任务名称带有计划前缀：`[计划标题] 任务名称`
   - 保存每个任务的专属提示词以维护上下文

3. **智能调度**
   - **BFS模式**：基于优先级执行 `(priority ASC, id ASC)`
   - **DAG模式**：依赖感知的拓扑排序，支持循环检测
   - 支持全局执行和特定计划执行

4. **上下文汇集** (`app/services/context.py`)
   - **全局索引**：始终包含`INDEX.md`作为最高优先级上下文
   - **依赖关系**：收集`requires`和`refers`链接的任务
   - **计划兄弟**：包含同一计划中的相关任务
   - **TF-IDF检索**：在现有任务输出中进行语义搜索
   - **手动选择**：用户指定的任务

5. **预算管理** (`app/services/context_budget.py`)
   - **基于优先级的分配**：`index > dep:requires > dep:refers > retrieved > sibling > manual`
   - **多级限制**：总字符预算 + 单节字符限制
   - **智能摘要**：句子边界截断或直接截断
   - **确定性**：相同输入产生相同结果

6. **执行与存储**
   - LLM执行，带重试逻辑和指数退避
   - 上下文快照，确保可重现性
   - 结构化输出存储和元数据

### 数据模型

```sql
-- 核心任务管理
tasks (id, name, status, priority)
task_inputs (task_id, prompt)
task_outputs (task_id, content)

-- 依赖图
task_links (from_id, to_id, kind)  -- kind: requires/refers

-- 上下文快照
task_contexts (task_id, label, combined, sections, meta, created_at)
```

### 调度算法

**BFS调度（默认）**

```python
def bfs_schedule():
    rows = default_repo.list_tasks_by_status('pending')
    # 稳定排序：(priority ASC, id ASC)
    rows_sorted = sorted(rows, key=lambda r: (r.get('priority') or 100, r.get('id')))
    yield from rows_sorted
```

**DAG调度（依赖感知）**

```python
def requires_dag_order(title=None):
    # 1. 从task_links构建依赖图（kind='requires'）
    # 2. 使用Kahn算法进行拓扑排序
    # 3. 同级任务按优先级打破平局
    # 4. 循环检测并提供详细诊断
```

### 上下文智能

**多源上下文汇集**

```python
def gather_context(task_id, include_deps=True, include_plan=True, k=5, tfidf_k=None):
    sections = []
    
    # 全局INDEX.md（最高优先级）
    sections.append(index_section())
    
    # 依赖关系（requires > refers）
    deps = repo.list_dependencies(task_id)
    sections.extend(dependency_sections(deps[:k]))
    
    # 计划兄弟任务
    siblings = repo.list_plan_tasks(title)
    sections.extend(sibling_sections(siblings[:k]))
    
    # TF-IDF语义检索
    if tfidf_k:
        retrieved = tfidf_search(query, k=tfidf_k)
        sections.extend(retrieved_sections(retrieved))
    
    return {"sections": sections, "combined": combine(sections)}
```

**TF-IDF检索算法**

- 支持中英文的文档分词
- 带平滑的IDF计算：`log(1 + N/(1 + doc_freq))`
- 按文档长度的TF标准化
- 可配置的分数阈值和候选限制

## 🔧 API参考

### 计划管理端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/plans/propose` | POST | 从目标生成任务计划 |
| `/plans/approve` | POST | 批准并持久化计划 |
| `/plans` | GET | 列出所有现有计划 |
| `/plans/{title}/tasks` | GET | 获取特定计划的任务 |
| `/plans/{title}/assembled` | GET | 获取计划汇总输出 |

### 执行端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/run` | POST | 执行任务，支持完整配置 |
| `/tasks` | POST | 创建单个任务 |
| `/tasks/{id}/output` | GET | 获取任务输出 |

### 上下文管理

| 端点 | 方法 | 描述 |
|------|------|------|
| `/context/links` | POST/DELETE | 管理任务依赖关系 |
| `/context/links/{task_id}` | GET | 查看任务关系 |
| `/tasks/{task_id}/context/preview` | POST | 预览上下文汇集 |
| `/tasks/{task_id}/context/snapshots` | GET | 列出上下文快照 |

### 全局索引

| 端点 | 方法 | 描述 |
|------|------|------|
| `/index` | GET | 获取全局INDEX.md |
| `/index` | PUT | 更新全局INDEX.md |

## ⚙️ 配置

### 环境变量

**LLM配置**

```bash
GLM_API_KEY=your_api_key                    # 生产环境必需
GLM_API_URL=https://open.bigmodel.cn/...   # API端点
GLM_MODEL=glm-4-flash                       # 模型名称
LLM_MOCK=1                                  # 启用开发模式
LLM_RETRIES=3                               # 重试次数
LLM_BACKOFF_BASE=0.5                       # 指数退避基数（秒）
```

**上下文与检索**

```bash
TFIDF_MAX_CANDIDATES=500                    # TF-IDF候选池大小
TFIDF_MIN_SCORE=0.0                         # 最低相关性分数
GLOBAL_INDEX_PATH=/path/to/INDEX.md         # 全局索引文件位置
```

**调试**

```bash
CTX_DEBUG=1                                 # 启用上下文汇集调试日志
CONTEXT_DEBUG=1                             # 启用上下文服务调试日志
BUDGET_DEBUG=1                              # 启用预算管理调试日志
```

### 上下文选项

```json
{
  "context_options": {
    "include_deps": true,          // 包含依赖任务
    "include_plan": true,          // 包含计划兄弟任务
    "k": 5,                        // 每类别最大数量
    "manual": [1, 2, 3],           // 手动指定任务ID
    
    "tfidf_k": 2,                  // TF-IDF检索数量
    "tfidf_min_score": 0.15,       // 最低相关性分数
    "tfidf_max_candidates": 200,   // 候选池大小
    
    "max_chars": 1200,             // 总字符预算
    "per_section_max": 300,        // 单节字符限制
    "strategy": "sentence",        // 摘要策略
    
    "save_snapshot": true,         // 保存上下文快照
    "label": "experiment-1"        // 快照标签
  }
}
```

## 🛠️ CLI使用

### 基础执行

```bash
# 执行所有待处理任务
conda run -n LLM python agent_cli.py

# 执行特定计划，带上下文
conda run -n LLM python agent_cli.py --execute-only --title "我的计划" \
  --use-context --schedule dag

# 完整配置示例
conda run -n LLM python agent_cli.py --execute-only --title "研究项目" \
  --schedule dag --use-context \
  --tfidf-k 2 --tfidf-min-score 0.15 --tfidf-max-candidates 200 \
  --max-chars 1200 --per-section-max 300 --strategy sentence \
  --save-snapshot --label experiment-1
```

### 上下文快照管理

```bash
# 列出任务的快照
conda run -n LLM python agent_cli.py --list-snapshots --task-id 12

# 导出快照到文件
conda run -n LLM python agent_cli.py --export-snapshot \
  --task-id 12 --label experiment-1 --output snapshot.md
```

### 全局索引管理

```bash
# 预览INDEX.md（不写文件）
conda run -n LLM python agent_cli.py --index-preview

# 导出到指定路径
conda run -n LLM python agent_cli.py --index-export /path/to/INDEX.md

# 生成并持久化，记录历史
conda run -n LLM python agent_cli.py --index-run-root
```

## 🧪 测试

### 运行测试套件

```bash
# 快速测试运行（使用mock LLM）
conda run -n LLM python -m pytest -q

# 带覆盖率报告
conda run -n LLM python -m pip install pytest-cov
conda run -n LLM python -m pytest --cov=app --cov-report=term-missing
```

### 开发模式

```bash
export LLM_MOCK=1
# 现在所有LLM调用都返回确定性的mock响应
```

## 🏗️ 架构设计

### 模块化设计

- **接口层** (`app/interfaces/`)：LLM和Repository的抽象基类
- **数据层** (`app/repository/`)：SQLite实现的数据访问层
- **服务层** (`app/services/`)：业务逻辑（计划、上下文、预算）
- **调度器** (`app/scheduler.py`)：任务排序算法
- **执行器** (`app/executor.py`)：带上下文汇集的任务执行
- **工具类** (`app/utils.py`)：共享工具（JSON解析、前缀处理）

### SOLID原则实现

- **单一职责**：每个服务都有专注的目的
- **开闭原则**：通过接口实现可扩展
- **里氏替换**：Mock和真实实现可互换
- **接口隔离**：专注的接口（LLMProvider、TaskRepository）
- **依赖倒置**：服务依赖抽象，不依赖具体实现

### 关键设计模式

- **Repository模式**：数据访问抽象
- **依赖注入**：可测试的服务组合
- **策略模式**：可插拔的上下文源和预算策略
- **模板方法**：一致的执行工作流

## 🚀 部署

### 生产环境考虑

- 设置适当的`GLM_API_KEY`并配置重试/退避参数
- 使用`GLOBAL_INDEX_PATH`指定持久索引位置
- 根据LLM token限制配置上下文预算
- 启用结构化日志以提高可观测性

### Docker部署（可选）

```dockerfile
FROM python:3.11-slim
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ app/
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 🤝 贡献

1. Fork仓库
2. 创建特性分支
3. 为新功能添加测试
4. 确保所有测试通过`pytest`
5. 提交Pull Request
---

**基于现代AI编排原则构建**：智能上下文管理、依赖感知调度和生产就绪架构，适用于可扩展的LLM任务自动化。