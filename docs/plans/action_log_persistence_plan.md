# Action Log Persistence & Reply Summary Plan

## 背景与目标
- **现状问题**
  - `StructuredChatAgent` 的动作执行日志仅存在内存中，任务完成后无法追溯。
  - LLM 回复未明确总结本轮触发的动作，用户/前端很难理解系统执行情况。
  - PlanExecutor、plan/task 操作等后台 Job 缺乏统一的日志持久化与前端展示。
- **目标**
  1. **持久化动作日志**：同步/异步执行的每个动作都写入可长期存储的 SQLite（按 plan 隔离，planless 会话写入系统库）。
  2. **统一查询接口**：通过 `/jobs/{id}` 或 `/plans/{plan_id}/jobs/{job_id}` 获取日志、状态、摘要，支持实时 SSE 与历史回看。
  3. **LLM 回复总结**：在 `llm_reply` 中附带结构化的“动作摘要”，帮助用户理解执行流程。
  4. **前端展示一致化**：聊天气泡、计划执行面板等位置统一消费日志与摘要，支持分页、折叠、导出。
  5. **运维保障**：提供迁移、清理、监控与测试策略，确保日志持久化稳定可靠。

## 总体架构
```
StructuredChatAgent/_execute_action
  └─ append_action_log(...)  # plan_storage or system_jobs
  └─ emit SSE via plan_decomposition_jobs (已有)
PlanExecutor / Chat Action Jobs
  └─ 使用同一 helper 写日志 + 状态

SQLite (per-plan) ─── plan_action_logs
                    └─ plan_job_index (扩展 job_type/status)
system_jobs.sqlite ── plan_action_logs (plan_id = NULL)

API 层
  ├─ GET /jobs/{id}?cursor=...
  ├─ GET /plans/{plan_id}/jobs/{job_id}/actions
  └─ SSE /jobs/{id}/stream

Frontend
  ├─ useJobLog(jobId) hook
  ├─ JobLogPanel (通用组件)
  └─ ChatMessage metadata.actions_summary 展示
```

## 后端实现细节

### 1. 数据库 Schema
- **新表 `plan_action_logs`**（每个 plan DB + `system_jobs.sqlite` 各自拥有）：
  | 列名 | 类型 | 说明 |
  | --- | --- | --- |
  | `id` | INTEGER PRIMARY KEY | 自增 |
  | `plan_id` | INTEGER NULL | plan 绑定，为空表示 system job |
  | `job_id` | TEXT NOT NULL | 关联 job（chat_action、plan_decompose 等） |
  | `session_id` | TEXT NULL | 聊天会话 ID |
  | `user_message` | TEXT NULL | 触发动作的用户消息（可截断） |
  | `action_kind` | TEXT NOT NULL | 例如 `plan_operation` |
  | `action_name` | TEXT NOT NULL | 例如 `create_plan` |
  | `status` | TEXT NOT NULL | `queued`/`running`/`completed`/`failed` |
  | `success` | INTEGER NULL | 布尔，已完成才填 |
  | `message` | TEXT NULL | LLM/执行器返回的信息（已脱敏/截断） |
  | `details_json` | TEXT NULL | 精简后的详情 JSON |
  | `sequence` | INTEGER NOT NULL | 同一 job 内顺序自增 |
  | `created_at` | TEXT NOT NULL | ISO 时间 |
  | `updated_at` | TEXT NOT NULL | ISO 时间 |
- **索引**
  - `(job_id, sequence)`：保证顺序查询。
  - `(plan_id, created_at)` + `(session_id, created_at)`：支持历史查询。
- **Schema 版本**
  - `plan_storage` 维护 `schema_version`，升级脚本在访问时自动创建表/索引。
  - 写入变更记录到 `database_schema_overview.md`。

### 2. Repository 层
- 新增接口（`app/repository/plan_storage.py`）：
  - `append_action_log(plan_id, log_entry)`：返回 sequence。
  - `list_action_logs(plan_id, job_id, limit=200, cursor=None)`：分页查询；`cursor` 采用 `(sequence, created_at)`。
  - `cleanup_action_logs(plan_id, older_than_days, max_rows)`：定时清理。
  - 同样提供 `system` 版本：`append_action_log_system(...)` 等。
- 防止重复：
  - 以 `job_id + sequence` 唯一约束；sequence 通过 `COALESCE(MAX(sequence),0)+1` 获取并包裹在事务内。
- **字段裁剪/脱敏 helper**
  - `_redact_log_payload(details: Dict[str, Any]) -> Dict[str, Any]`：
    - 删除 `api_key`、`authorization`、`headers` 等敏感键。
    - 限制字符串长度（例如 4096），超出则截断并注明。
    - 对大型数组（>50 项）取前若干并记录总数。

### 3. Agent & Job 执行路径
- **StructuredChatAgent**
  - `_execute_action`：  
    1. `append_action_log(... status="running" ...)` 在动作执行前写入一条记录。  
    2. 成功后 `append_action_log(... status="completed", success=True, message=...)`。  
    3. 异常时捕获 -> `append_action_log(... status="failed", success=False, message=repr(exc))` -> 重新抛出。
  - `_handle_tool_action` 写入的 `summary`、`details` 先用 `_redact_log_payload`。
  - 未绑定 plan：调用 system 版本存储，`plan_id` 使用 `None`。
- **后台 Job（PlanExecutor、LLM chat_action 等）**
  - 在统一的 `job_runner` helper 内注入 `job_id`，对每个动作调用同一 `append_action_log`。  
  - `plan_decomposition_jobs.append_log` 同时调用 `append_action_log`，保证实时日志 & 永久日志一致。
- **LLM 回复摘要**
  - `execute_structured` 结束后，根据 `steps` 生成结构化数组 `actions_summary`：  
    ```json
    {
      "actions_summary": [
        {"order": 1, "display": "plan_operation/create_plan → 成功", "success": true, "message": "..."},
        ...
      ]
    }
    ```
  - 将该数组写入 `AgentResult.metadata`（新字段），并拼接为自然语言段落追加到 `llm_reply.message` 尾部（可配置）。
  - 增加设置 `settings.chat.include_action_summary`，默认 `True`。

### 4. API 调整
- **新/改路由**
  - `GET /jobs/{job_id}`：返回 `job`, `logs`（默认最近 50 条）、`next_cursor`、`actions_summary`。  
    - 若 job 绑定 plan，内部选择相应 plan DB；否则查 system 库。
  - `GET /plans/{plan_id}/jobs/{job_id}/actions`：分页返回日志，支持 `cursor`, `limit` 参数。  
  - `GET /jobs/{job_id}/stream`：SSE，事件包含 `job`, `log_entry`, `cursor`；完成后发送 `status=completed/failed` 并关闭。
- **兼容性**
  - 原 `/tasks/decompose/jobs/{id}` 返回格式扩展 `logs` & `actions_summary` 字段。旧前端读不到也不会报错。
  - 所有接口在 miss 时返回 404 + `{"error": "not_found"}`。

### 5. 迁移与清理
- 启动时：
  1. 检测 plan DB schema 版本 < 新版本 → 执行迁移（创建表/索引）。
  2. `system_jobs.sqlite` 同步迁移。
- **迁移脚本**：`scripts/migrate_action_logs.py`，支持批量迁移 & dry-run。
- **清理策略**：  
  - 每晚定时任务（`scripts/cleanup_jobs.py`）删除超过 30 天或单 job 超过 10,000 条的日志。  
  - 清理同时更新 `plan_job_index`，避免悬挂引用。
- 在 `docs/data_migration_policy.md` 记录升级路径与回滚方式。

## 前端实现细节

### 1. Hook & 状态管理
- 新增 `useJobLog(jobId, initialJob?)`：  
  1. `fetchJob(jobId, cursor)` 拉取快照。  
  2. 打开 SSE（若支持）；收到 `log_entry` 就 append。  
  3. SSE 断线 → 每 5s 轮询一次 `/jobs/{id}?cursor=...`，最多重试 5 次。  
  4. job 完成或失败 → 关闭 SSE，切换为只读模式。
- 将 `actions_summary` 存入聊天 store，用于气泡渲染。

### 2. UI 组件
- `JobLogPanel`
  - 通用属性：`jobId`, `expectType`, `initialJob`, `planId`.  
  - 顶部 Header 显示 job 类型、状态、开始/结束时间。  
  - 日志列表采用虚拟滚动或分页，默认展示最新 200 条，可“加载更多”。  
  - 提供导出按钮（下载 JSON/文本）。
  - 显示错误/重试提示（SSE 断线、404 等）。
- `ChatMessage` 更新：  
  - 若 metadata 包含 `type="job_log"` + `job_id` → 渲染 `JobLogPanel`。  
  - `metadata.actions_summary` → 渲染在气泡底部，可折叠。  
  - 后续 PlanExecutor 等消息共用同一组件。

### 3. 国际化与文案
- 摘要文案默认中文，可扩展 i18n（使用现有国际化机制）。  
- 平台级配置决定是否在聊天窗口内展示“动作摘要”模块。

## 配置 & 安全
- 新增 `settings.chat.include_action_summary`、`settings.jobs.log_retention_days` 等配置项。
- 日志写入前必须调用 `_redact_log_payload`，并在 Repo 层再做一次保险过滤。
- SSE 响应需设置 `Cache-Control: no-cache`、`Connection: keep-alive`，并对跨域情况配置 CORS。

## 测试计划
- **单元测试**
  - Repo：动作日志写入/读取/分页/清理、planless fallback。
  - Redaction：敏感字段被移除、字符串截断。
  - Agent：模拟成功/失败动作，验证写入顺序与 metadata。
- **集成测试**
  - 聊天流程：发送消息 → create_plan → web_search → 验证 `/jobs/{id}` 返回日志 & 摘要。
  - PlanExecutor：执行计划，确保日志/状态被前端读取。
  - SSE：启动后台 job，订阅流并断线重连，验证重入逻辑。
- **前端测试**
  - Hook：模拟分页、SSE 事件、404。
  - 组件：确保不同 `job_type`、成功/失败状态显示正确。

## 上线步骤
1. 后端完成实现与测试，提交 PR。
2. 执行迁移脚本，备份旧数据库。
3. 更新前端 Hook & UI，联调 `/jobs` 接口。
4. 在测试环境验证长任务、断网、历史查询等场景。
5. 上线后监控：
   - 日志写入错误率、数据库膨胀状况。
   - SSE 失败率、前端重试次数。
6. 两周后复盘，评估保留期/分页策略是否需要调整。

## 依赖与风险
- SQLite 写入频率上升：需要关注潜在锁竞争，可通过批量事务或异步写入缓冲优化。
- 日志量较大时磁盘占用增加：清理策略必须按计划执行。
- LLM 摘要可能造成回复过长：需在系统提示或配置中提供关闭选项。

## 参考实现清单
- [ ] Repo：`append_action_log` / `list_action_logs` / redaction helper。
- [ ] Agent：日志写入、LLM 摘要生成。
- [ ] PlanExecutor & JobManager：统一日志调用。
- [ ] API：`GET /jobs/{id}`、`/jobs/{id}/stream`、`/plans/{id}/jobs/{job_id}/actions`。
- [ ] 前端：`useJobLog` hook、`JobLogPanel`、聊天 UI 更新。
- [ ] 文档更新：`database_schema_overview.md`、`frontend_api_usage.md`、`plan_decomposition_async_logging.md`。

