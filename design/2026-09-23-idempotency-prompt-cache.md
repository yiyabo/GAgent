# 可靠性与成本改造计划：消息幂等 + Prompt Cache 稳定化

> 日期：2026-09-23
> 背景：架构对比审计（GAgent vs Hermes / Codex / ZCode）认定的两个最高优先级项：
> ① 断线重试导致 Agent 重复执行（数据正确性）；② 对话历史拼进 system prompt 导致
> prompt cache 全灭 + 32k 预算对百万窗口模型过保守（成本）。
> 原则：**行为可独立上线、独立回滚；每一步带测试。**

---

## 任务 1：消息幂等（堵重复执行）

### 现状问题
- 前端 `streamChatEvents`（旧 `/chat/stream`，无会话路径）失败重试 = 整体重发 POST ×3，
  每次后端 `save_user_message=True` 重复落库 + Agent 完整重跑（工具副作用重复）。
- `chat_runs.idempotency_key` 列已存在但从未写入；`_save_chat_message` 无去重。
- `ChatRequest.client_message_id` 前端已生成、后端已透传（消息 metadata），链条只差"接线"。

### 设计（已锁定）
1. **DB**：`chat_runs(session_id, idempotency_key)` 加部分唯一索引（`WHERE idempotency_key IS NOT NULL`）。
   生产库该列全 NULL，建索引无冲突。
2. **`start_background_chat_run`**：
   - `client_message_id` 存在 → 先查同 `(session_id, key)` 的既有 run：
     - `queued`（崩在 create_task 前）→ 补 spawn worker 后返回原 run_id；
     - `running` / 终态 → 直接返回原 run_id（客户端走 seq 重放拿结果）。
   - 未找到 → 正常建 run，`idempotency_key=client_message_id` 落库；
     并发重复 INSERT 撞唯一索引 → 捕获后走"查找既有"路径。
3. **`_save_chat_message`**：metadata 含 `client_message_id` 时先按
   `json_extract(metadata,'$.client_message_id')` 查同会话既有消息，命中即返回原 id
   （跳过 insert/记忆中间件/session 时间戳）。覆盖 run 路径与 legacy 无会话路径。
4. **前端 `streamChatEvents`**：去掉整包重发重试（默认 1 次尝试），失败走既有
   `_recoverAfterStreamFailure` / 错误 UI。run 路径本来就是"建 run 一次 + 事件流重连"，安全。
5. **`streamRunEvents` / `useMessages` 的裸 fetch 补 `credentials: 'include'`**
   （Cookie 鉴权部署下续传/历史 401 的修复）。
6. **`/chat/runs/{id}/events` 补 SSE keepalive**：把 `_sse_with_keepalive` 从 `stream.py`
   下移到 `background.py`（消除 stream↔run_routes 循环 import），events 生成器包装之，
   5s 空闲发 `: keepalive`，防 nginx 60s 切断长工具静默期。

### 测试
- 仓储级：idempotency_key 写入 + 唯一索引冲突；按键查 run。
- `start_background_chat_run`：同 key 重复调用 → 同 run_id、用户消息只一条；
  queued 状态重入 → worker 补启动。
- `_save_chat_message`：同 client_message_id 第二次调用返回原 id 且不重复 insert。
- keepalive：慢源下收到 `: keepalive` 注释行（若已有 `_sse_with_keepalive` 测试则复用）。

---

## 任务 2：Prompt Cache 稳定化 + 预算模型化

### 现状问题
- `_append_reference_context`（prompts.py:731）把最近 80 条历史以文本形式拼进
  **system prompt**（native 与 prompt-based 双路同此），每轮变化 → cache 前缀全灭。
- `DEEP_THINK_CONTEXT_BUDGET_TOKENS` 固定 32k（controller.py:132），对 qwen3.7-max
  （1M 窗口）过保守 → 频繁触发有损 LLM 摘要，白花钱又丢上下文。
- prompt-based 路径无 compaction（仅 native 有）。

### 设计（已锁定）
1. **历史出 system prompt**：prompts.py 新增 `_extract_history_messages(context)`，
   复用现有限流/裁剪逻辑（80 条上限、500 字裁剪、brief-execute 6 条/240 字），
   产出 `[{role, content}]` 消息列（仅 user/assistant，其余角色跳过）。
2. **双路控制器**（native controller.py:121、prompt-based controller.py:1385）：
   `messages = [system, *history_msgs, user]`。历史进入 messages 后自然纳入
   native 路径的 `compact_if_needed` 视野，两套上下文机制合一。
3. `_append_recent_chat_history` 保留（facade 包装与既有测试依赖），但提示词构建链
   不再调用；`_append_reference_context` 末尾不再拼历史。
4. **变量块保持原位**（acceptance spec、recent tool results 等仍在 system 尾部）——
   稳定前缀 = 指令 + 工具 schema + 协议边界（大头），本轮只做收益最大的历史迁移，
   不重排块顺序（控制风险）。
5. **预算模型化**：`_resolve_context_budget(model)`：
   - env `DEEP_THINK_CONTEXT_BUDGET_TOKENS` 显式设置 → 优先（运维旋钮，行为不变）；
   - 否则按模型窗口表 × `DEEP_THINK_CONTEXT_BUDGET_RATIO`（默认 0.5），
     下限 32000（对小窗口模型不退化），上限 `DEEP_THINK_CONTEXT_BUDGET_MAX`（默认 131072）。
   - 窗口表（前缀匹配，保守只列确认值）：`qwen3.7-max`→1_000_000，`qwen-max`→262_144，
     `gpt-4o`→128_000；未命中 → 128_000。
   - qwen3.7-max 实效果：min(500k, 131072) = **131072**（现 32k 的 4 倍）。

### 测试
- 提示词构建：system prompt 不含 `RECENT CONVERSATION` / 历史内容；
  双路 messages 中历史以角色对形式出现在 user 之前；裁剪/限流语义不变；
  `_append_recent_chat_history` 直调行为不变（兼容）。
- 预算解析：env 覆盖 / 表命中 / 兜底 / 下限上限夹取各分支。
- 全量基线比对（Mac 存量 37 失败，必须 git stash -u 后对比）。

---

## 风险与回滚
| 风险 | 缓解 |
|---|---|
| 历史进 messages 改变 native 压缩触发点 | 全量测试 + 基线比对；生产观察首轮 token |
| 唯一索引在生产建失败 | 列全 NULL，部分索引无冲突；bundle 部署前在 .8 容器内先执行 DDL 验证 |
| legacy 路径去掉重试后弱网首消息失败率升 | 错误 UI 已引导重发；run 路径不受影响 |
| 预算放大导致单轮 token 上涨 | ratio/上限均为 env 旋钮，可即时回调 32k |

## 部署顺序
1. 后端（幂等 + keepalive + 提示词/预算）→ 测试绿 → bundle → .8 → 查在跑 run → restart → 冒烟。
2. 前端（重试/credentials）→ 显式置空构建 → `:9000` 零命中 → tar 传 .8（不重启容器）。
3. 五方同步 + LOCAL_INFRA 补记。
