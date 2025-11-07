# Plan/Task 实时同步方案

## 背景
- 聊天端在执行 `create_plan`、`decompose_task` 等动作后，前端需要立即刷新计划树、任务列表，否则用户看不到最新结果。
- 目前仅有泛化的 `tasksUpdated` 事件，缺少细粒度类型，异步任务完成通知也不够明确，导致自动刷新不稳定。

## 目标
1. 同步/异步动作完成后，相关视图无需手动刷新即可看到最新计划/任务变更。
2. 保持现有 API（`/chat/message`、`/jobs/{id}`、`/plans/{id}/tree` 等）不变。
3. 复用现有 `tasksUpdated` 事件机制，补充精细化类型，避免跨模块强耦合。

## 方案概览

### 1. 后端（保持现状）
- `StructuredChatAgent` 在同步路径返回 `actions`、`actions_summary`，异步路径在 `ActionStatusResponse.result.steps` 中列出各动作。
- Job 机制（`plan_decomposition_jobs`）在任务完成时提供 `status`、`plan_id`、`result` 等字段。
- 无需增加新接口，只要前端解析已有返回即可。

### 2. 前端统一派发逻辑（`web-ui/src/store/chat.ts`）
1. **判定动作类型**  
   - 解析 `result.actions` / `status.actions`：判定 `plan_operation` 与 `task_operation` 中的 `create_*`、`update_*`、`delete_*`、`decompose_*` 等。
   - 提取 `plan_id`：优先取 `metadata.plan_id` → `actions` 参数 → `steps[].details`。

2. **事件格式**（示例）
   ```ts
   type PlanSyncEvent =
     | { type: 'plan_created'; plan_id: number; plan_title?: string | null }
     | { type: 'plan_deleted'; plan_id: number }
     | { type: 'plan_updated'; plan_id: number }
     | { type: 'task_changed'; plan_id: number }
     | { type: 'plan_jobs_completed'; plan_id: number; job_type: string }
     | { type: 'session_deleted'; session_id: string };
   ```

3. **派发时机**
   - 同步执行：`sendMessage` 在添加 assistant 消息后立即派发（无 `tracking_id`）。
   - 异步执行：`waitForActionCompletion` 在轮询拿到最终状态后派发。
   - 后台 Job（拆分/执行等）：`JobLogPanel` 监听 SSE，当 `status` 变为 `succeeded` 或 `failed` 时派发 `plan_jobs_completed`。

4. **防重与日志**
   - 在 `chat.ts` 记录最近一次 `(type, plan_id, tracking_id)`，500ms 内重复忽略。
   - `console.info` 打印派发详情便于诊断。

### 3. 事件消费方

| 模块                              | 响应逻辑                                                         |
| --------------------------------- | ---------------------------------------------------------------- |
| `useTasksStore` (`tasks.ts`)      | 新增统一监听逻辑，根据 `detail.type` 调用 `planTreeApi` 重新获取。<br> - `plan_created`：刷新计划列表并选中新计划。<br> - `plan_deleted`：若当前 plan 命中则清空任务视图。<br> - `task_changed`：`getPlanTree(plan_id)` + 更新 DAG。<br> - `plan_jobs_completed`：刷新计划树和统计。 |
| `TaskDetailDrawer.tsx`            | 仍监听 `tasksUpdated`，但只处理 `detail.plan_id` 匹配的事件。   |
| `Plans.tsx` / `DAGVisualization`  | 根据 `plan_id` 区分，避免不同 tab 互相影响。                     |
| 其它监听 `tasksUpdated` 的组件    | 识别新类型后可选择忽略或执行轻量刷新。                           |

### 4. 用户体验
- 创建计划后聊天气泡出现的同时，“计划列表”立即新增并选中该计划，任务树加载完成。
- 拆分任务/执行计划完成后，当前 plan 视图自动刷新，无需点击“刷新”按钮。
- 后台 job 日志面板可提示“已同步最新计划树”，提高操作可见度。

### 5. 实施步骤
1. `chat.ts`：新增 `dispatchPlanSyncEvent` 助手，整合同步/异步派发；完善 `waitForActionCompletion` 触发点；在 SSE job 成功时调用。
2. `useTasksStore`：在 store 初始处集中监听 `tasksUpdated`，根据类型调用 `planTreeApi.listPlans()`、`planTreeApi.getPlanTree()` 等；清理原本分散在多处的监听重复逻辑。
3. `TaskDetailDrawer`/`TreeVisualization` 等组件：保留监听，但改为判断 `detail.type`，避免无关刷新。
4. 验证流程：  
   - 同步 `create_plan` → 计划列表立即变化。  
   - 异步 `decompose_task` → job 完成后任务树变更。  
   - 删除计划 → 当前视图回退并提示。  
   - 观察 console/Network，无多余请求。

### 6. 后续可选优化
- 利用 `plan_action_logs` 实现增量 patch（只更新受影响节点）。  
- 将事件机制封装为 `usePlanSyncEvents` Hook，便于复用与测试。  
- 对于 SSE job，可以 push 事件类型，减少前端推断。

## 风险与规避
- **重复刷新**：通过防抖缓存避免重复事件多次触发。  
- **plan_id 缺失**：解析失败时应回退为全量刷新并记录日志。  
- **兼容性**：`tasksUpdated` 原有监听仍可收到老格式事件；新 detail 类型对旧代码透明。  
- **异步竞态**：确保派发前添加用户消息，避免 UI 阻塞；在等待 job 时做好超时提示。

## 验收标准
1. 同步创建/更新/删除计划后，Plan 页面无需手动刷新即可反映最新结构。  
2. 异步分解、执行完成后，任务树和执行统计自动更新。  
3. “计划列表” 与 “任务详情抽屉” 状态一致，无脏数据。  
4. 开发者查看 console 时能看到清晰的事件日志，排查方便。
