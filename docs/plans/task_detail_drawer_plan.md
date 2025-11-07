# 对话界面任务详情弹窗方案

## 目标
- 在聊天界面旁的任务图谱中点击任意任务时，弹出一个抽屉式详情面板，集中展示任务的完整信息。
- 提供任务执行结果、元数据、上下文等关键信息的即时查看与刷新能力。
- 与现有的计划数据流和 `tasksUpdated` 事件保持一致，最小化对现有交互的影响。

## 交互流程
1. 用户在任务图谱（`TreeVisualization` / `DAGVisualization`）点击节点。
2. 组件调用回调 `onNodeSelect(taskId)`，将选中任务 ID 写入全局 store，并打开抽屉。
3. 抽屉第一次打开时：
   - 使用 `usePlanTasks` 得到基础任务信息（含 `metadata`、`context_*`、`execution_result` 等字段）。
   - 并行调用 `GET /tasks/{task_id}/result?plan_id=...` 以获取最新执行结果，结果缓存在前端 store。
4. 弹窗关闭时清理 `selectedTaskId`，保持图谱状态不变。
5. 后续若触发 `tasksUpdated` 事件且该计划包含正在查看的任务，则自动刷新任务信息与执行结果。

## 前端实现要点
- **状态管理**：在 `useTasksStore` 或新建的 `usePlanUIStore` 中维护：
  - `selectedTaskId: number | null`
  - `isTaskDrawerOpen: boolean`
  - `taskResultCache: Record<number, PlanResultItem>`
- **抽屉组件**：推荐使用 AntD `Drawer`（宽度≈480px），内部通过 `Descriptions` / `Collapse` 呈现。
- **信息区块**：
  1. 基础信息：任务名、ID、父任务、层级、状态徽标、创建/更新时间。
  2. 属性信息：指令、依赖、位置、`task_type` 等。
  3. 上下文 & Metadata：`context_combined`（折叠展示）、`context_sections`、`context_meta`、`metadata`。
  4. 执行结果：从缓存或最新接口获取，展示 `status` / `content` / `notes` / `metadata`，提供原始 JSON 折叠查看。
  5. 操作按钮：刷新结果、复制任务信息；预留执行/重试入口。
- **文本展示**：采用 `Typography.Paragraph` 搭配 `ellipsis` 与 `copyable`，确保长文本可折叠与复制。
- **依赖跳转**：依赖列表可关联点击回调，驱动图谱高亮或切换到相应任务。

## API 使用
- `GET /plans/{plan_id}/tree`：初始任务信息来源（已在 `usePlanTasks` 中使用）。
- `GET /tasks/{task_id}/result?plan_id={plan_id}`：获取最新执行结果。
- 现有 `tasksUpdated` 浏览器事件可用于在后台执行完成后自动刷新抽屉内容。

## 事件同步策略
- 监听 `tasksUpdated` 事件，当 `detail.plan_id` 与当前计划匹配时：
  - 重新拉取 `usePlanTasks` / `usePlanResults`；
  - 若抽屉打开并显示目标任务，则刷新执行结果接口并更新缓存。

## 增量实施步骤
1. 扩展任务图谱组件，暴露 `onNodeSelect` 并取消现有详情面板依赖。
2. 在全局 store 新增任务抽屉状态与执行结果缓存。
3. 新建 `TaskDetailDrawer` 组件，按照上述结构渲染信息。
4. 在聊天页面引入该抽屉组件，与任务图谱选中事件打通。
5. 实现“刷新结果”“复制任务”等操作，并接入 `tasksUpdated` 自动刷新逻辑。
6. 视情况扩展执行/重试按钮，与后台 action 流程对接。

## 后续拓展
- 支持任务编辑、删除、上下文追加等快捷入口。
- 增加执行结果历史记录或上下文版本对比。
- 允许从抽屉内快速跳转到父/子任务或依赖任务的详情视图。
