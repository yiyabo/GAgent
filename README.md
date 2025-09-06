# 🧠 AI‑Driven 智能任务编排系统

将“自然语言目标”转为“可执行计划并产出高质量结果”的一体化系统。具备分解→调度→上下文→执行→评估→装配的全链路能力，支持工具增强与多评估模式。

## 🧭 核心理念（Core Principles）

- 单一事实来源（SSOT）：配置集中（services/foundation/settings.py），避免散落的环境读取；嵌入/评估参数统一入口。
- 分层解耦：foundation / llm / embeddings / context / evaluation / planning / memory 明确边界，职责单一、内部可替换。
- 评估驱动：以“结果质量”为系统闭环核心，内置多模式评估与监督，支持可重复、可审计。
- 工具增强最小充分：仅在必要处启用工具（信息/产出），在成本、时延、质量间做平衡。
- 可观测/可重现：结构化日志、SQLite 存储与快照、可配置 Mock，便于开发与诊断。

## ✨ 核心特性

### 🚀 智能编排
- **计划生成**：从高层目标自动产出任务树
- **递归分解**：Root → Composite → Atomic 三级分解，复杂度评估与深度控制
- **依赖感知**：DAG/BFS/后序调度，循环检测与稳定顺序
- **上下文智能**：全局索引 + 依赖/同计划/层级 + 语义检索，预算裁剪

### 🎯 质量评估
- **LLM 评估**：6 维质量评分与建议，支持迭代改进
- **多专家评估**：多角色协作打分与共识
- **对抗评估**：生成器/批评者博弈提升鲁棒性
- **评估留痕**：历史/配置/统计齐全

### 🧰 工具增强（Tool Box）
- 智能路由是否使用外部工具
- 信息工具丰富上下文（如搜索/数据库），产出工具落地（如写文件）

### ⚡ 可靠性
- 多层缓存与 SQLite 存储
- 上下文快照与可重现执行
- 完整测试与可选 Mock 模式（开发场景）

## 🚀 快速开始

### 环境准备
```bash
# 激活conda环境
conda activate LLM

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export GLM_API_KEY=your_key_here
# 可选：开发/离线使用模拟模式
# export LLM_MOCK=1
```

### 启动 API 服务
```bash
# 生产（需配置真实 API Key）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 开发（可用 Mock）
# LLM_MOCK=1 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 🔧 分解与执行（推荐后序调度）
```bash
# 单任务分解（标准/工具感知/带评估）
curl -X POST http://localhost:8000/tasks/123/decompose \
  -H "Content-Type: application/json" \
  -d '{"max_subtasks": 5, "force": false, "tool_aware": true}'

# 计划级递归分解
curl -X POST http://localhost:8000/plans/MyReport/decompose -H "Content-Type: application/json" -d '{"max_depth": 3}'

# 执行（自动分解 + 工具增强 + 评估）
curl -X POST http://localhost:8000/run -H "Content-Type: application/json" -d '{
  "title": "MyReport",
  "schedule": "postorder",
  "use_context": true,
  "auto_decompose": true,
  "decompose_max_depth": 3,
  "use_tools": true,
  "enable_evaluation": true,
  "evaluation_mode": "llm",
  "evaluation_options": {"max_iterations": 3, "quality_threshold": 0.8},
  "context_options": {"max_chars": 8000, "strategy": "sentence"}
}'
```

### 🔍 仅评估模式（三种）
```bash
# LLM：evaluation_mode=llm
# 多专家：evaluation_mode=multi_expert
# 对抗：evaluation_mode=adversarial
```

## 🏗️ 系统架构

### 目录分层（Services）

```
app/services/
  foundation/   # 配置、日志、类型化参数（SSOT）
  llm/          # LLM 统一服务与响应缓存
  embeddings/   # 嵌入服务、批处理、缓存（线程安全）
  context/      # 上下文组装、语义检索、结构先验
  evaluation/   # 评估器、多专家/对抗、监督与缓存
  planning/     # 计划生成与递归分解（含工具感知）
  memory/       # 记忆子系统（MCP 集成）
  legacy/       # 低频/实验/过渡模块（后续清退）
```

### 分层数据流（ASCII）

```
+----------------------+     +-------------------+     +----------------------+
|     Client (CLI/UX)  | --> |   FastAPI app     | --> |     Scheduler        |
|  curl / script / UI  |     |  app/main.py      |     |  BFS / DAG / postord |
+----------+-----------+     +-----+-------------+     +----------+-----------+
           | CLI/REST         CRUD | Tasks/Plans          | execution order
           v                        v                     v
+----------+-----------+     +-----+-------------+     +----------+-----------+
| Planning / Decompose | <--> |   Repository     | <--> | Executors / LLM      |
| services/planning    |     | SQLite (tasks,   |     | execution/enhanced   |
+----------------------+     | outputs, eval)   |     +----------+-----------+
                                  |     ^                      |
                                  |     |                      |
                         +--------+-----+--------+     +------+--------------+
                         |   Context Builder     |     |  Evaluation System  |
                         | services/context      |     | services/evaluation |
                         +----------+------------+     +----------+----------+
                                    |                           |
                                    | embeddings/similarity     |
                                    v                           |
                         +----------+------------+              |
                         |  Embeddings Service   | <------------+
                         | services/embeddings   |
                         +-----------------------+
```

### 核心工作流程
```
目标输入 → 计划生成 → 人工审核 → 计划批准 → 任务调度 → 上下文组装 → 预算控制 → 评估执行 → 结果组装
```

### 评估系统分层架构
```
执行层: executor_enhanced.py (支持4种评估模式)
├── 基础执行 | LLM评估 | 多专家评估 | 对抗性评估
评估器层: services/
├── LLM评估器 | 多专家评估器 | 对抗性评估器 | 元认知评估器
优化层: 
├── 缓存系统 (evaluation_cache.py) 
└── 监督系统 (evaluation_supervisor.py)
```

### 关键组件

**1. 智能任务分解**
- **ROOT任务**: 高复杂度项目，自动分解为主要功能模块 (深度0)
- **COMPOSITE任务**: 中等复杂度任务，分解为具体实现步骤 (深度1) 
- **ATOMIC任务**: 低复杂度任务，可直接执行的最小单元 (深度2)
- **智能评估**: 基于关键词密度和描述长度的复杂度评估
- **质量控制**: 子任务数量、名称质量、类型一致性检查
- **深度限制**: 最大分解深度3层，防止过度细分

**2. 上下文感知系统**
- **全局索引**: 总是包含 `INDEX.md` 作为最高优先级上下文
- **依赖关系**: 收集 `requires` 和 `refers` 链接的任务
- **计划兄弟**: 来自同一计划的相关任务
- **语义检索**: 基于嵌入/相似度的跨任务检索

**3. 评估模式选择**
| 内容类型 | 推荐模式 | 特点 |
|---------|----------|------|
| 简单文档 | 基础评估 | 快速、高效 |
| 专业内容 | LLM智能评估 | 深度理解、智能建议 |
| 重要文档 | 多专家评估 | 多角度验证、专业意见 |
| 关键内容 | 对抗性评估 | 最高质量、鲁棒性强 |

**4. 工具增强（Tool Box）**
- 智能分析是否需要工具；按需调用“信息工具”丰富上下文，再执行生成；最后调用“产出工具”保存/落地。
- 也可通过 `/tasks/{id}/execute/tool-enhanced` 对单任务增强执行。

## 🔀 ASCII 系统流程图

```
+---------------------+        +------------------+        +---------------------+
|      客户端         |  HTTP  |     FastAPI      |  调度  |      Scheduler      |
|  (CLI / REST / UI)  +------->+   app/main.py    +------->+  BFS / DAG / 后序   |
+----------+----------+        +---------+--------+        +----------+----------+
           |                            |                              |
           | CLI参数/REST Body          |   计划/任务/上下文/评估API   | 产出待执行任务序列
           v                            v                              v
+----------+----------+        +---------+--------+        +----------+----------+
|   Planning/Plan     |        |    Repository    |        |   Executor/LLM      |
|  提议/批准/计划管理 |<------>+  SQLite (tasks,  +<-------+ execution/executors |
+---------------------+  CRUD  |  outputs, eval)  |  读写  |  base/enhanced      |
                               +---------+--------+        +----------+----------+
                                         |                             |
                                         | 上下文组装/预算裁剪         | LLM生成/严格评估
                                         v                             v
                               +---------+--------+        +----------+----------+
                               | Context Builder  |        | Evaluation System   |
                               | services/context |        | (LLM/多专家/对抗)   |
                               +---------+--------+        +----------+----------+
                                         |                             |
                                         +-------------+---------------+
                                                       |
                                               +-------+---------+
                                               |  输出汇总/基准  |
                                               | MD / CSV / 指标 |
                                               +-----------------+
```

## 📚 文档导航

- 快速开始：`docs/QUICK_START.md`
- 递归分解：`docs/RECURSIVE_DECOMPOSITION_GUIDE.md`
- 评估系统：`docs/EVALUATION_SYSTEM.md` / `docs/EVALUATION_SYSTEM_GUIDE.md`
- API 参考：`docs/API_REFERENCE.md`
- 存储与缓存：`docs/Database_and_Cache_Management.md`
- Memory‑MCP：`docs/MEMORY_MCP_SYSTEM.md`

## 🎨 使用示例

### 📊 API工作流程
```bash
# 1. 提议计划
curl -X POST http://127.0.0.1:8000/plans/propose \
  -H "Content-Type: application/json" \
  -d '{"goal": "Write a technical whitepaper on gene editing"}'

# 2. 批准计划
curl -X POST http://127.0.0.1:8000/plans/approve \
  -H "Content-Type: application/json" \
  --data-binary @plan.json

# 3. 执行（启用上下文感知和评估）
curl -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Gene Editing Whitepaper",
    "schedule": "dag",
    "use_context": true,
    "evaluation_mode": "llm",
    "context_options": {
      "include_deps": true,
      "tfidf_k": 2,
      "max_chars": 1200
    }
  }'
```

## 📈 性能指标

- **评估准确性**: > 85% (LLM评估 vs 人工评估一致性)
- **系统响应时间**: < 10秒 (缓存命中时)
- **缓存命中率**: > 60% (减少重复计算)
- **系统可用性**: > 99% (生产环境稳定性)

## 🔧 运行示例

- 可通过 `/plans/propose` → `/plans/approve` → `/run` 完成端到端演示
- 若仓库未包含示例脚本或 examples 目录，参考上文 cURL 命令即可

## 🚨 故障排除

### 评估速度慢？
```bash
# 检查缓存状态（新路径）
LLM_MOCK=1 python -c "from app.services.evaluation.evaluation_cache import get_evaluation_cache; print(get_evaluation_cache().get_cache_stats())"

# 优化缓存
LLM_MOCK=1 python -c "from app.services.evaluation.evaluation_cache import get_evaluation_cache; get_evaluation_cache().optimize_cache()"
```

### 评估质量不稳定？
```bash
# 查看监督报告
LLM_MOCK=1 python -m cli.main --eval-supervision --detailed

# 检查系统统计
LLM_MOCK=1 python -m cli.main --eval-stats --detailed
```

### 数据库损坏（database disk image is malformed）？
```bash
rm -f tasks.db tasks.db-shm tasks.db-wal
python -c "from app.database import init_db; init_db(); print('DB initialized')"
```

## 🛠️ 技术栈

- **Python 3.8+** - 核心编程语言
- **FastAPI** - 高性能Web框架
- **SQLite** - 数据存储和缓存
- **大语言模型** - GLM API智能评估
- **TF-IDF** - 语义相似度检索
- **多线程** - 并发任务处理

## 📋 版本摘要（最近）

- /run 新增编排开关：`auto_decompose`、`use_tools`、`evaluation_mode`、`decompose_max_depth`
- 工具 + 评估合流：先信息工具增强上下文，再迭代评估生成，最后产出工具

## 🤝 贡献指南

欢迎贡献代码和建议！

### 开发环境设置
```bash
# 克隆仓库
git clone <repository-url>
cd agent

# 激活conda环境
conda activate LLM

# 安装依赖
pip install -r requirements.txt

# 运行测试（使用模拟模式）
LLM_MOCK=1 python -m pytest tests/ -q

# 运行示例验证
LLM_MOCK=1 python examples/evaluation_examples.py --example all
```

### 代码规范
- 遵循 PEP 8 代码风格
- 添加类型注解和文档字符串
- 编写单元测试和集成测试
- 更新相关文档

## 🙏 致谢

感谢所有贡献者和用户的支持，让这个项目不断完善和发展。

## 📄 许可证

本项目采用 MIT 许可证。详见 `LICENSE` 文件。

---

**🚀 AI-Driven智能任务编排系统 v2.0** - 让AI任务编排更智能、更准确、更可靠

*最后更新时间: 2025年*

---

## 🗺️ Roadmap / TODO

短期（S）
- S1: 交互式对话任务构建系统（会话式 Goal→Plan，实时预览与编辑、一步生成/多步细化）
- S2: Agent 效果评测基线（统一评测框架 + 指标：质量/事实性/效率/成本），对比 GPT / Claude / Gemini / Grok 等
- S3: Pydantic v2 迁移（ConfigDict + pydantic-settings），消除弃用告警；测试覆盖补齐

中期（M）
- M1: 针对“噬菌体”领域的外源知识图谱构建与检索增强（领域 Schema、实体对齐、引证与可追溯）
- M2: 评估监督可视化（dashboard）与结果复用（跨项目重用、版本化）
- M3: 去除兼容别名（services/__init__.py），统一新分层导入路径，文档同步

长期（L）
- L1: 多 Agent 协同与角色分工（策划/执行/评估/审校），策略优化（自适应工具使用）
- L2: 插件化生态（评估器/检索器/执行器），企业级配置中心与多租户隔离
