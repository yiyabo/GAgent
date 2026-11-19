# 通过 MCP 向 GAgent 提交分析/建模任务：设计

状态：设计稿，待 w 审阅。没有实现，也没有改动线上。接在 [PhageScope MCP 设计](2026-11-19-phagescope-mcp.md)（PR #169）和一期只读 `/mcp`（PR #172）之后。

## 1. 背景与先例

- 已上线：`https://phageagent.deepomics.org/mcp` 提供 11 个只读 PhageScope 查询工具，使用按用户发放的 `gpsk_` 令牌，有限额和审计。
- w 的决定：**PhageScope 数据永远不能通过 MCP 写入**。本设计只新增"让外部 Agent 把分析/建模任务交给 GAgent 去做"。
- 先例：把 Agent 当作 MCP 工具给别的 Agent 用已经很常见，例如 FutureHouse/Edison（科研 Agent 各做成一个工具，可续问）、Galaxy MCP、Seqera MCP（提交、跟踪、取结果）、GitHub Copilot（`create_pull_request_with_copilot` 立即返回 job ID，再用 `get_copilot_job_status` 查）、Devin MCP、Exa `deep_researcher_start/check`、mcp-agent（`run/get_status/resume/cancel`）。MCP 2025-11-25 规范新增的 Tasks（SEP-1686）就是为这种场景准备的，但目前仍是实验特性，客户端支持很少。Codex 弃用 `codex mcp-server` 的原因之一，就是同步 tool call 撑不住长任务。
- 由此得出的做法：**提交后立即返回 task_id，任务在后台跑，客户端轮询状态并取结果**。用普通工具实现，所有 MCP 客户端都能用；状态值与 MCP Tasks 对齐，以后客户端支持了再直接映射过去。

## 2. 目标与非目标

目标：
1. 外部 Agent（Codex、Claude Code、Hermes、Devin 等）用一句自然语言，把 PhageScope 数据分析、统计、建模任务交给 GAgent；GAgent 用现有的 DeepThink、沙箱代码执行和 bio_tools 完成，并交回结论和产物。
2. 任务归属到令牌对应的 GAgent 用户，网页端能看到同一条会话，可以接着人工跟进。
3. 安全：PhageScope 只读；按用户限额；不能形成 Agent 互相回调的循环；可审计。

非目标（本期）：修改 PhageScope 数据；大文件直传（> 5 MB）；OAuth 网页连接器（沿用 #169 的四期）；A2A 协议端点（列为后续可选）。

## 3. 复用的现有机制（已核对代码）

| 需要的能力 | 复用 | 位置 |
|---|---|---|
| 后台持久运行、崩溃恢复、租约 | durable chat run：`start_background_chat_run`（按 `client_message_id` 幂等）、`execute_chat_run` 工作线程和租约心跳 | `app/routers/chat/run_routes.py`、`app/services/chat_run_worker.py` |
| 状态机 | `queued → running → succeeded/failed/cancelled`，单一写入口 | `app/services/chat_run_state.py` |
| 取消 | 先写持久信号 `insert_chat_run_signal(run_id, "cancel")`，再走进程内取消 | `run_routes.cancel_run` |
| 中途补充信息 | `steer_run` | `run_routes.py` |
| 超时 | `run_deadline_exceeded` → `failed` | `chat_run_state.ChatRunOutcome` |
| 产物 | 会话 deliverables 清单和文件下载 | `app/routers/artifact_routes/` |
| 令牌、限额、审计 | `phagescope_mcp_tokens/usage/audit` 表、`limiter`、`record_audit` | `app/services/phagescope_mcp/` |

结论：**不需要新的执行引擎**。MCP 层只是一个很薄的适配层：把工具调用转成"以该用户身份发起一次 chat run"，再把 run 的状态和产物转回来。

## 4. 架构

```
外部 Agent ──Bearer gpsk_… (scope analysis:submit)──> /mcp（同一端点）
   submit_analysis ──> 新建会话（owner = 令牌用户，origin = mcp）──> start_background_chat_run ──> DeepThink / 沙箱 / bio_tools / phagescope_data(只读)
   get_analysis_status / get_analysis_result <── chat_runs 状态 + 事件 + deliverables 清单
   reply_analysis ──> 同一会话里的下一次 run（或对运行中的 run 执行 steer）
   cancel_analysis ──> cancel 信号
```

- 每个 MCP 任务对应一个 GAgent 会话（session）。续问在同一会话里新起 run，上下文自然保留。
- 新表 `phagescope_mcp_analysis_tasks`：`task_id`（对外公开，`gpat_` + 随机串）、`token_id`、`user_id`、`session_id`、`current_run_id`、`status`、`title`、`created_at`、`updated_at`、`expires_at`。对外只暴露 task_id，不暴露内部 run_id 和 session_id。
- 会话标记 `origin=mcp`，并记录令牌名，方便网页端显示"来自 Codex"之类的标签，也方便统计。

## 5. 工具（只对带 `analysis:submit` scope 的令牌出现在 `tools/list` 里）

| 工具 | 输入 | 输出 | 注解 |
|---|---|---|---|
| `submit_analysis` | `instructions`（≤ 8,000 字），`phage_accessions[]`（可选，≤ 200 个），`inline_data`（可选，CSV/TSV/FASTA 文本，≤ 1 MB），`deliverables_hint[]`（可选，如 `table`、`figure`、`model`、`report`），`idempotency_key`（可选） | `task_id`、`status=working`、`poll_after_seconds`、`web_url` | 非只读、非破坏性；同一 `idempotency_key` 重试时返回原任务 |
| `get_analysis_status` | `task_id` | `status`（`working` / `input_required` / `completed` / `failed` / `cancelled`）、`phase`（如"规划"、"运行代码"、"汇总"）、最近 3 条进度说明、已用时长、`poll_after_seconds` | 只读、幂等 |
| `get_analysis_result` | `task_id` | `summary`（≤ 8k 字）、`findings[]`、`deliverables[]`（名称、类型、大小、sha256、15 分钟有效的签名下载链接）、`provenance`、`web_url`；未完成时返回 `not_ready` | 只读、幂等 |
| `reply_analysis` | `task_id`、`message` | 新的 `status=working` | 回答澄清问题，或在已完成的任务上续问 |
| `cancel_analysis` | `task_id` | `cancelled` 或 `cancel_requested` | 破坏性：只停止任务，不删除产物 |
| `list_analyses` | `status?`、`limit≤20` | 当前令牌的任务列表 | 只读 |

状态映射：`queued`/`running` → `working`；`succeeded` → `completed`；`failed`（包括超时）→ `failed`，并附原因；`cancelled` → `cancelled`。如果 run 正常结束，但最终答复是在向用户提问（现有 plan clarification 流程），就映射为 `input_required`，并在 status 里给出问题原文，用 `reply_analysis` 回答。

`provenance` 包含：GAgent 版本（pN）、PhageScope 快照（与一期同源）、使用的模型名、调用过的工具和次数、运行时长。结论里的预测类字段沿用一期约定，注明来源。

## 6. 权限与安全

1. **PhageScope 只读**：MCP 发起的 run 只能使用现有的只读数据通道（`phagescope_data` v2 和 gateway 查询）。`phagescope` 工具的 `submit` action（向 PhageScope 网站提交分析任务）在 `origin=mcp` 时禁用。写入只发生在该用户自己的会话工作区。
2. **防循环**：`origin=mcp` 的 run 禁用用户挂载的外部 MCP 扩展（`app/services/extensions`），也不能调用本机 `/mcp`，所以 GAgent 不会回调外部 Agent 或自己。
3. **独立 scope**：`analysis:submit` 默认不发放。只有管理员能在令牌上加这个 scope，白名单仍用 `PHAGESCOPE_MCP_ALLOWED_USERS`。旧令牌只有 `data:read`，行为不变。
4. **输入边界**：`inline_data` 落到会话工作区作为附件，与网页上传走同一套检查；本期不接受 URL 抓取，避免 SSRF 和带宽滥用。
5. **输出当作不可信数据**：结果只给结构化摘要和下载链接，不回传原始日志或大段工具输出，以减少对调用方 Agent 的提示注入面。签名链接只能下载该任务的产物。
6. **审计**：每次调用写入现有 `phagescope_mcp_audit`；另外记录 task_id 与 run_id 的对应关系，便于在 Run Inspector 里排查。

## 7. 配额（默认值，可通过 env 调整）

| 项 | 默认 | 说明 |
|---|---|---|
| 每令牌同时运行 | 1 | 超出时新任务排队（`working`，phase=`queued`） |
| 每令牌排队上限 | 3 | 超出时返回 `too_many_tasks` |
| 每用户每日提交 | 10 | UTC 0 点重置 |
| 全局 MCP 来源同时运行 | 2 | 避免挤占网页用户 |
| 单任务时长 | 沿用 run deadline | 超时按 `failed` 处理，产物保留 |
| 产物下载 | 计入一期的每日字节限额 | 与只读查询共用 |
| 任务保留 | 30 天 | 到期后 task_id 失效，会话按常规策略保留 |

`get_analysis_status` 本身也受一期的每分钟请求限制；返回的 `poll_after_seconds`（默认 15 s，长任务逐步增加到 60 s）用来引导客户端不要频繁轮询。

## 8. 配置

`PHAGESCOPE_MCP_ANALYSIS_ENABLED=0`（默认关闭），`PHAGESCOPE_MCP_ANALYSIS_MAX_RUNNING_PER_TOKEN`、`..._MAX_QUEUED_PER_TOKEN`、`..._DAILY_SUBMISSIONS`、`..._GLOBAL_MAX_RUNNING`、`..._RETENTION_DAYS`。全部走 `get_settings()`。

## 9. 代码落点（实现阶段）

- `app/services/phagescope_mcp/analysis.py`：工具定义，以及任务与 run 的转换（新文件，预计 < 600 行）。
- `app/services/phagescope_mcp/store.py`：新表和 scope 常量 `SCOPE_ANALYSIS_SUBMIT`。
- `server.py`：`tools/list` 和 `tools/call` 按 scope 合并两组工具。端点入口目前要求 `data:read`，改为"至少有一个已知 scope"。
- `app/routers/chat/run_routes.py`：`start_background_chat_run` 已可直接调用，不需要改；`origin=mcp` 通过 `ChatRequest.context` 传入，DeepThink 工具目录据此禁用第 6 节列出的工具。
- 网页：令牌页显示 scope；会话列表显示 MCP 来源标签（小改动）。

## 10. 测试与验证

- 单测：用 stub 替代 worker，覆盖 scope 可见性、幂等提交、状态映射（含超时和 `input_required`）、取消、配额、签名链接过期、`origin=mcp` 的工具禁用。
- 集成：在隔离栈上用桩 LLM 跑完整流程（提交 → 轮询 → 取结果 → 续问 → 取消）。
- 客户端试用：在 Codex 和 Claude Code 上各跑 5 个真实任务，例如"统计 Salmonella 烈性噬菌体的基因组长度分布并画直方图"、"比较两个宿主属的 GC 含量并做显著性检验"、"用 PhageScope 特征训练一个生活方式分类器并报告交叉验证结果"。记录成功率、轮询次数、耗时和体验问题。

## 11. 分期

1. **MVP**：`submit_analysis`、`get_analysis_status`、`get_analysis_result`，加上 scope、配额、`origin=mcp` 限制和审计。
2. `reply_analysis`、`cancel_analysis`、`list_analyses`，以及 `input_required` 映射。
3. 文件上传（预签名上传接口，≤ 100 MB）。
4. 客户端普遍支持后，声明 MCP Tasks capability，让 `tools/call` 直接返回 task；如有需要，再加 A2A Agent Card 端点，复用同一套任务后端。

## 12. 待 w 决定（括号内为默认）

1. 分析工具放在同一 `/mcp` 端点，按 scope 显示；还是另开 `/mcp-agent`（默认同一端点，客户端只需配一次）。
2. 谁能拿到 `analysis:submit`（默认只给管理员手动加，先给 w 自己和少数试用者）。
3. 配额默认值（第 7 节）是否合适，尤其是全局同时运行数 2。
4. MCP 任务的 LLM 费用记在谁头上：令牌所属用户，与网页使用一样（默认是）。
