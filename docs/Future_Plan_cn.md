# 未来规划：具备上下文感知的 LLM 任务运行器路线图

## 0. 概述
- 目的：构建一个具备上下文感知与依赖敏感的任务运行器，将大的目标拆解为若干“大任务”，最终细化为最小可执行单元（MEU）。每个 MEU 可在执行时获得正确的上下文，该上下文来自任务/文档图谱的精心汇集，并可选择性地接受人工干预。
- 关键主题：基于图的上下文、人在环、确定性调度、可复现运行、可扩展架构。

## 1. 当前状态（基线）
- 计划分组：`utils.plan_prefix()` 与 `utils.split_prefix()`。
- 任务 I/O 持久化：`task_inputs`、`task_outputs`。
- 执行：`executor.execute_task()` 仅读取任务自身的输入作为提示，不含依赖/上下文拼装。
- 调度：`scheduler.bfs_schedule()` 依据 `(priority, id)` 排序；不具备依赖意识。
- 服务：规划服务可提出/批准计划并为任务生成初始提示。

## 2. Phase 0（已完成）
- 移除模块级仓库包装器；全局使用 `default_repo` 实例。
- 引入 `app/utils.py` 并去重工具函数（`plan_prefix`、`split_prefix`、`parse_json_obj`）。
- 清理缓存并改进 `.gitignore`。测试通过，代码已推送。

## 3. Phase 1 — 上下文图谱基础（已完成）
目标：引入轻量的任务关系有向图，并提供基本 API 以管理与查询链接。在执行前启用最小可用上下文拼装。

- 大任务（Big Tasks）
  1) 数据模型：新增 `task_links` 表（from_id, to_id, kind）。
  2) 仓库：实现链接的 CRUD 与基础图查询。
  3) 服务：`context.gather_context(task_id, ...)` 汇集上下文包。
  4) API：提供新增/删除/查询链接与上下文预览的端点。
  5) 执行器：可选在提示前加入拼装好的上下文。

- 小任务（Small Tasks）
  - 数据库迁移：`task_links(from_id INTEGER, to_id INTEGER, kind TEXT, PRIMARY KEY(from_id,to_id,kind))`（使用 IF NOT EXISTS，惰性创建）。
  - 仓库接口（扩展 `TaskRepository`）：
    - `create_link(from_id: int, to_id: int, kind: str) -> None`
    - `delete_link(from_id: int, to_id: int, kind: str) -> None`
    - `list_links(from_id: int | None = None, to_id: int | None = None, kind: str | None = None) -> List[Dict]`
    - `list_dependencies(task_id: int) -> List[Dict]`（支持 `requires`、`refers`）
  - `app/services/context.py`：
    - `gather_context(task_id: int, include_plan: bool = True, include_deps: bool = True, k: int = 5, manual: List[int] | None = None) -> Dict`
    - 汇集：同计划的兄弟任务（短内容）、依赖节点（全量/短内容）、手动指定节点。
  - API 路由（如 `app/main.py` 或 `app/routes/context.py`）：
    - `POST /context/links`（创建）、`DELETE /context/links`（删除）、`GET /context/links/{task_id}`（查询链接）
    - `POST /tasks/{task_id}/context/preview` -> 返回拼装的上下文包
  - `app/executor.py`：
    - 可选标志启用上下文（`use_context=True`），按模板将上下文段落置于任务提示之前。
  - 测试：FakeRepo 扩展链接方法；为汇集器和端点添加单元/端到端测试。

- 交付物（Deliverables）
  - 最小的图编辑与读取 API；基本上下文包可被执行器消费。
  - 暂无嵌入，确保确定性行为。

- 验收标准（Acceptance Criteria）
  - 能够以 `requires` 或 `refers` 链接任务，并通过 API 验证。
  - 执行器在启用时能包含确定性的上下文段落。
  - 测试覆盖：链接 CRUD、`gather_context`、以及一次使用链接上下文的端到端运行。

## 4. Phase 2 — 上下文选择、预算与摘要(待完成)
目标：在给定 Token/字符预算内选择最相关的上下文；可选加入语义检索。

- 大任务
  1) 上下文预算管理器（Token/字符配额）。
  2) 针对长内容的摘要生成。
  3) 可选语义检索（先 TF-IDF，后续再考虑嵌入）。
  4) 缓存上下文快照以便复现。

- 小任务
  - `context.budget.py`：基于优先级的贪心分配（依赖 > 计划兄弟 > 语义命中 > 手动补充）。
  - 摘要器实现：先启发式，后可选 LLM 摘要。
  - TF-IDF 基线：在 `task_outputs` 上进行检索。
  - 可选嵌入层与索引（如 `sentence-transformers`）。
  - 持久化 `task_contexts(task_id, compiled_context, created_at)`。

- 交付物
  - 可配置的上下文策略，具备预算与摘要能力。
  - 在无 LLM 摘要时保持确定性（固定种子）。

- 验收标准
  - 在给定预算下，`gather_context` 返回的上下文包不超限。
  - 对超长项产生摘要；选择顺序可测试。

## 5. Phase 3 — 依赖感知调度（待完成）
目标：仅调度其 `requires` 依赖已满足的任务；能检测并报告环。

- 大任务
  1) 使用 `task_links(kind='requires')` 构建 DAG。
  2) 采用稳定的拓扑调度（按优先级与 id 打破平局）。
  3) 环检测与报告；提供手动覆盖钩子。

- 小任务
  - 新调度器：`requires_dag_schedule()`。
  - 集成到 `/run`，通过标志控制（`strategy='dag'|'bfs'`）。
  - 测试：DAG 顺序、环检测、部分完成场景。

- 交付物
  - 确定性、依赖感知的执行。

- 验收标准
  - 依赖未满足的任务不会被调度。
  - 可识别环并提供可操作的诊断信息。

## 6. Phase 4 — 根任务与索引文档（待完成）
目标：将根任务视为可执行单元；生成高层索引（`INDEX.md`）与全局约定。

- 大任务
  1) 根计划操作：搭建项目结构与全局规则。
  2) 持久化 `INDEX.md`（作为任务输出），并标记为全局上下文源。

- 小任务
  - 根任务的规划模板。
  - `gather_context`：始终以最高优先级纳入 `INDEX.md`。
  - API：获取与更新 `INDEX.md`。

- 交付物
  - 可执行的根任务，产出在全局使用的索引文档。

- 验收标准
  - 含根上下文的运行表现能与索引规则保持一致。

## 7. Phase 5 — 人在环（Human-in-the-Loop）（待完成）
目标：允许用户对上下文与依赖进行引导。

- 大任务
  1) 链接管理的 UI 钩子（先从 API 层面）。
  2) 上下文预览、剪枝与固定（pin）。

- 小任务
  - `POST /tasks/{id}/context/preview` 返回候选上下文包，并附带 pin/unpin 标记。
  - `approve_context` 端点应用人工调整。

- 交付物
  - 透明、可审计的上下文拼装过程，支持人工介入。

- 验收标准
  - 操作者可增删引用并确定性复现。

## 8. Phase 6 — 可观测性与运行可复现（待完成）
目标：让每次运行都可追踪并可复现。

- 大任务
  1) 对提示、上下文包、模型与输出进行结构化日志记录。
  2) 持久化运行记录与每次执行的上下文快照。

- 小任务
  - 新增 `runs` 表：task_id、started_at、finished_at、status、used_context_id、model/config。
  - 请求/响应日志，支持敏感信息脱敏。

- 验收标准
  - 任何输出都可以追溯到其上下文与参数。

## 9. Phase 7 — 质量、工具与 CI/CD（待完成）
目标：系统增长过程中保持质量。

- 大任务
  1) 测试覆盖率提升（服务、调度、上下文选择）。
  2) Lint/format（ruff/black）、类型检查、pre-commit 钩子。
  3) GitHub Actions：push 时跑测试；可选构建/上传制品。

- 验收标准
  - 主分支保持绿色 CI；设置最低覆盖率目标（如 80%）。

## 10. 数据模型（草案）
- `tasks(id, name, status, priority)`
- `task_inputs(task_id, prompt)`
- `task_outputs(task_id, content)`
- `task_links(from_id, to_id, kind)` —— 链接种类：`requires`、`refers`、`duplicates`、`relates_to`
- 可选：`task_contexts(id, task_id, payload, created_at)`
- 可选：`runs(id, task_id, used_context_id, started_at, finished_at, status, model, config)`

## 11. API 草图（新增/更新）
- 链接（Links）
  - POST `/context/links` { from_id, to_id, kind }
  - DELETE `/context/links` { from_id, to_id, kind }
  - GET `/context/links/{task_id}` -> { incoming: [...], outgoing: [...] }
- 上下文（Context）
  - POST `/tasks/{task_id}/context/preview` { options } -> bundle
  - POST `/tasks/{task_id}/context/approve` { pins, excludes } -> 应用人工覆盖
- 运行（Run）
  - POST `/run` { title?, strategy?, use_context?, options? }

## 12. 执行提示模板（草案）
```text
你将完成以下任务：
任务：{task_name}

上下文（按优先级排序）：
{对每个条目}
- 来源：{task_id 或 INDEX.md} | 类型：{requires|refers|plan|manual}
  摘要：{截断或已摘要的内容}

指令：
- 如实使用信息；通过任务 id 引用来源。
- 若上下文存在冲突，优先级顺序：requires > plan index > manual > refers。
- 保持输出简洁且可执行。
```

## 13. 各阶段大/小任务分解
- Phase 1（图谱基础）
  - 大任务：DB 模型 + 仓库 + 上下文服务 + API + 执行器集成
  - 小任务：迁移、链接 CRUD、汇集器、端点、测试
- Phase 2（选择与预算）
  - 大任务：预算管理器 + 摘要器 +（可选）检索器 + 上下文快照
  - 小任务：TF-IDF 基线、配置开关、测试
- Phase 3（DAG 调度）
  - 大任务：`requires`-DAG + 拓扑调度 + 环检测
  - 小任务：调度策略标志、单测/端测
- Phase 4（根任务与索引）
  - 大任务：根任务流程 + `INDEX.md` 集成
  - 小任务：模板、包含策略、测试
- Phase 5（人在环）
  - 大任务：预览/审批工作流
  - 小任务：pin/unpin、手工编辑、测试
- Phase 6（可观测性）
  - 大任务：运行记录 + 结构化日志
  - 小任务：脱敏、追踪、测试
- Phase 7（质量/CI）
  - 大任务：CI + 覆盖率 + 钩子

## 14. 迁移与兼容性
- 非破坏性：Phase 1–2 仅新增表/接口；默认行为保持不变，除非显式启用。
- 迁移：幂等的 `CREATE TABLE IF NOT EXISTS`，配合版本表。

## 15. 风险与应对
- 需求蔓延：为各阶段设定明确的验收标准与测试。
- Token 膨胀：实施预算与摘要。
- 图谱误用：尽早校验链接种类并进行环检测。
- 可复现性：为每次运行快照上下文与配置。

## 16. 粗略时间表（可调整）
- Phase 1：1–2 天
- Phase 2：2–3 天
- Phase 3：1–2 天
- Phase 4：1 天
- Phase 5：1 天
- Phase 6–7：各 1–2 天

## 17. 未决问题
- 不同模型的上下文限制？（由提供商配置）
- 嵌入提供商选择与隐私约束？
- 人工覆盖的持久化模型（按运行 vs 按任务）？
- 多计划引用的权重分配？
