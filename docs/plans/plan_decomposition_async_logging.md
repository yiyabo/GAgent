# 计划分解异步化与实时日志面板一体化方案

## 目标与现状

- **痛点**：PlanDecomposer 运行时间长，前端串行等待易超时，用户看不到中途进度。
- **目标**：  
  1. 后端异步执行计划分解，立即响应。  
  2. 前端能够实时看到任务日志，并在聊天对话框中展示。  
  3. 任务完成后折叠日志，仅保留摘要。

## 后端：Job 管理与日志流

1. **Job Manager（已实现）**  
   - 位置：`app/services/plans/decomposition_jobs.py`。  
   - 功能：生成 `job_id`、记录状态/结果、对外序列化。  
   - `/tasks/{task_id}/decompose` 支持传入 `async_mode`，立即返回 Job 信息并将真实任务丢给 `BackgroundTasks`。
   - `/tasks/decompose/jobs/{job_id}` 可查询最新状态。

2. **实时日志缓存（待实现）**  
   - 在 JobManager 附近维护 `job_logs[job_id] = deque[...]`（限定长度）。  
   - 提供 `append(job_id, level, message)` 接口，后台执行流程按关键节点写入。  
   - 通过 `contextvars.ContextVar("current_job_id")` 或显式参数传递方式，在调用 PlanDecomposer 前设置当前 job_id，内部封装 `_log(job_id, ...)`，保证每条日志都能对应正确 job；如需复用 logging，可写一个自定义 Handler 读取 ContextVar，把记录写入缓冲区。

3. **事件流接口**  
   - 新增 `GET /tasks/decompose/jobs/{job_id}/stream`。  
   - 采用 **Server-Sent Events (SSE)** 优先（实现简单，自动重连），若未来需要双向通信再扩展 WebSocket。  
   - 推送 payload 示例：  
     ```json
     {
       "job_id": "abc123",
       "status": "running",
       "event": {
         "timestamp": "2024-06-10T08:00:00Z",
         "level": "info",
         "message": "已向 LLM 发送分解请求"
       },
       "stats": {"llm_calls": 1}
     }
     ```  
   - 后端在任务完成/失败时发送 `status` 变更，最后 `event` 可为空，仅告知状态。
   - FastAPI 实现要点：使用 `StreamingResponse` 包装一个 `async def event_generator()`；内部 `while True` 从队列读取事件、`yield f"data: {json}\n\n"`，并使用 `await asyncio.sleep` 防止阻塞事件循环；必要时添加心跳包（如 `event: ping`）。

4. **状态一致性**  
   - SSE 只负责推送日志+状态。  
   - 若前端断线，仍可通过轮询 `/jobs/{id}` 获取最终结果，SSE 只是增强体验。  
   - JobManager 缓存可设置过期策略（任务完成 N 分钟后清除）或未来迁移到 Redis；可提供 `finalize_job(job_id)` 在 `mark_success/mark_failure` 时写入完成时间，后台定时任务清理过期 job/日志。

## 前端：聊天气泡内嵌日志面板

1. **意图与 metadata**  
   - `executeTaskDecompose` 继续将 `job_id`、`job_status` 写入 `metadata`。  
   - 聊天消息渲染时检测 metadata：若存在 `job_id` 则渲染“任务执行”类型消息；建议 metadata 添加 `type: "job_log"` 或类似字段，避免 Markdown 渲染器误把日志区当普通文本。

2. **实时订阅 Hook**  
   - 新增 `useJobLogStream(jobId)`：  
     - 初始请求 `/tasks/decompose/jobs/{job_id}`，拿静态快照。  
     - 并行发起 SSE 连接，逐条接收日志 event → push 到内存状态。  
     - SSE 断线时 fallback 到定时轮询。  
     - `status` 变为 success/fail 时关闭订阅并返回最终结果。

3. **聊天 UI 结构**  
   - 消息主区域仍显示 LLM 的自然语言回复。  
   - 在同一气泡底部嵌入“实时日志面板”：  
     - 默认展开显示最近若干行。  
     - 顶部标题展示状态（进行中/成功/失败），带折叠按钮。  
     - 滚动超过上限时自动裁剪或折叠旧日志。  
     - 任务完成后自动折叠，只保留“查看详情”入口。

4. **兼容性与优化**  
   - 前端若检测到浏览器不支持 SSE，可直接进入轮询模式。  
   - 可考虑把日志面板抽象成独立组件，未来执行器、长耗时查询等场景也能复用。  
   - 支持手动刷新结果按钮，方便用户在异常情况下重新同步；对于长日志可在组件内维护最大展示数量（例如 200 条）并提供“导出全部”入口。

## 落地步骤建议

1. **后端**  
   - 扩展 JobManager：加入日志队列与写入工具函数。  
   - 后台执行流程（PlanDecomposer）各关键节点添加日志写入。  
   - 实现 `/stream` SSE 路由 + 基础心跳。

2. **前端**  
   - 创建 `useJobLogStream` 与 `JobLogPanel` 组件。  
   - 更新聊天消息渲染器，识别 metadata 并嵌入面板。  
   - 调整 `executeTaskDecompose`：在 LLM 回复中加入提示文案（例如“我正在后台拆分任务，请看实时日志”）。

3. **测试与监控**  
   - 编写端到端流程：触发分解 → 监听 SSE → 验证日志顺序与状态。  
   - 运营期观察 JobManager 内存占用，如需多实例部署改用共享缓存（Redis）；完成任务达到 TTL 后应调用清理逻辑释放内存。  
   - 日志量大时，可引入分页或“仅显示 N 条”策略，并确保 ContextVar/显式传参在多线程/协程环境下仍能正确分配 job。

## 常见扩展

- **多任务并发**：前端可同时监听多个 job_id，界面上以 tab/折叠列出。  
- **权限控制**：在 SSE 接口内校验当前会话/用户是否有权查看该 job。  
- **国际化**：日志模板使用代码内置英文，再在前端做多语言展示。

以上流程保证：后端异步执行 + 实时日志输出 + 聊天窗口内可视化，满足用户期待的“LLM 回复 + 进度面板”体验。

## 持久化扩展（计划内日志落地方案）

随着功能上线，用户希望即使后端重启或 job 过期也能回看历史拆分过程。结合现有按计划划分的 SQLite 存储结构，可以按以下步骤落地持久化：

1. **数据库结构**
   - 在 `initialize_plan_database` 中新增两张表，并在旧库上执行 `CREATE TABLE IF NOT EXISTS` 即时补全：
     ```sql
     CREATE TABLE IF NOT EXISTS decomposition_jobs (
       job_id TEXT PRIMARY KEY,
       mode TEXT NOT NULL,
       target_task_id INTEGER,
       status TEXT NOT NULL,
       error TEXT,
       params_json TEXT,
       stats_json TEXT,
       result_json TEXT,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       started_at TIMESTAMP,
       finished_at TIMESTAMP
     );

     CREATE TABLE IF NOT EXISTS decomposition_job_logs (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       job_id TEXT NOT NULL,
       timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       level TEXT NOT NULL,
       message TEXT NOT NULL,
       metadata_json TEXT,
       FOREIGN KEY(job_id) REFERENCES decomposition_jobs(job_id) ON DELETE CASCADE
     );
     ```
   - 表按 plan 拆分，天然隔离，不需要额外字段。

2. **JobManager 调整**
   - 每次 `create_job/mark_running/mark_success/mark_failure/append_log` 在调用内存逻辑的同时同步写入对应 plan 的 SQLite。
     - `create_job`：插入 `decomposition_jobs` 初始行，记录 mode/参数。
     - `append_log`：写入日志表，保证即使进程宕机也能重放。
     - 状态变更：更新 job 表的 `status/error/result_json/stats_json/started_at/finished_at`。
   - `get_job_payload` 为提升容错：若内存没有，则回退到 SQLite 读取、重建只读 job 实例并返回。这样前端可以查历史 job。
   - TTL 可以仅用于清理内存缓存；真正的历史清理逻辑改成定期删除 sqlite 中完成时间太久的 job（例如 30 天）或日志条数上限。

3. **接口兼容**
   - `/tasks/decompose/jobs/{job_id}` 优先查内存；若 miss 则直接返回持久化数据（含日志、结果、stats）。
   - `/stream` 在 job 已完成但仍有日志时，可先返回 snapshot，然后立即 `break`，避免长连；若前端想查看历史，可 fallback 到 GET 接口。

4. **前端体验**
   - `JobLogPanel` 先请求 GET 接口：如果拿到 snapshot（无论 job 是否还在运行）都能渲染历史日志；如果 404，提示“记录已被清理或不存在”。
   - 当 job 已结束且 SSE 不再可用时，面板进入只读模式；可加“导出日志”按钮从 snapshot 内的日志数组生成文件。
   - 超时/网络错误时给出明确提示并间隔重试（例如每 5s，最多 5 次），防止无限循环刷日志；超过展示阈值时提供“加载更多”或“导出全部”入口。

5. **迁移与回填**
   - 对于已存在但未持久化的历史 job，可考虑写一次性脚本读取留存的内存 dump/日志文件进行导入。
   - 文档应更新说明 TTL 仅适用于内存缓存，持久化的保留周期由定期清理策略决定。

这一方案兼容当前 per-plan SQLite 架构，后续若要水平扩展，只需把计划文件目录迁移到共享存储（或改用集中数据库）。这样既保留了实时日志体验，也满足“随时回看历史拆分”的需求。还需要说明：

- 无绑定计划的 job（例如跨计划工具）应写入共享 `system_jobs.sqlite`，保证持久化成功。
- 主库索引表在 job 过期归档时要同步清理（可与 plan 库数据清理同一批处理）。

## 前端统一可视化（适配全部 Job 类型）

随着 PlanExecutor、其它 LLM Action 也引入 Job 日志，需要把前端的实时面板组件抽象成通用版本，核心步骤如下：

1. **组件层拆分**
   - 将现有 `JobLogPanel` 抽象为 `JobLogPanel`（仅负责渲染 UI）与 `useJobLog(jobId)` hook（负责数据获取）。
   - Hook 输入 `jobId`、可选 `expectType`，返回 `{job, logs, status, missing}`。
   - UI 支持根据 `job.job_type` 展示不同的顶部标题/图标，例如：

     | job_type            | 图标/颜色 | 默认标题         |
     |---------------------|-----------|------------------|
     | `plan_decompose`    | 蓝色/拆分 | “任务拆分日志”   |
     | `plan_execute`      | 绿色/执行 | “计划执行日志”   |
     | `task_operation`…   | 橙色/工具 | “动作执行日志”   |

2. **消息渲染器适配**
   - 在 `ChatMessage` 内检测 metadata：`metadata.type === 'job_log'` 且存在 `job.job_type`。
   - 根据 job_type 渲染对应标签文案（例如“计划执行中”），但日志面板复用统一组件。
   - 支持折叠/展开、自动收起、copy 功能与拆分任务保持一致。

3. **其它入口**
   - 计划详情页 / 执行历史页可直接使用 `JobLogPanel`，只需传入 `jobId`。
   - 若 PlanExecutor 页面已有“执行进度”视图，可将日志面板嵌在侧边栏，显示同一 Job 日志。

4. **状态处理**
   - Hook 内部逻辑：
     - 先 `GET /jobs/{id}`，若 404 显示“已清理/不存在”。
     - 若 job 状态为 `succeeded/failed` 并且 `logs` 已完整，则不再发起 SSE，直接展示静态内容。
     - SSE/轮询与拆分任务一致，遇到 `job_type` 不匹配时可提示“日志类型不符或接口错误”，并限制最大重试次数（如 5 次）后停止拉取或退回只读模式。

5. **视觉一致性**
   - 使用 antd `Card` + `Tag` + `Timeline`（或列表）统一样式；颜色映射可复用 status/level。
   - 提供“导出日志”为 JSON/文本的按钮，方便排查问题。

6. **兼容旧消息**
   - 若旧 metadata 没有 `job_type`，默认视为 `plan_decompose`；随着后端更新，新的消息会带完整字段。

这样前端可以对所有带 Job 的动作提供一致的“实时 + 历史”可视化体验，用户看到的 UI 与拆分任务完全一致，只是标题/细节随 job_type 变化。
