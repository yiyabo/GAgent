# Web Search 结果呈现方案

本文档说明如何在现有聊天/计划界面中展示 `web_search` 工具返回的数据，确保与后端结构化动作流程保持一致。后端已在同步、异步路径中写入 `metadata.tool_results`（参考 `app/routers/chat_routes.py:481`），下面仅描述前端改动与交互设计。

---

## 1. 数据模型扩展

1. **类型定义**  
   - 更新 `web-ui/src/types/index.ts`：  
     ```ts
     export interface ToolResultItem {
       title?: string | null;
       url?: string | null;
       snippet?: string | null;
       source?: string | null;
     }

     export interface ToolResultPayload {
       name?: string | null;
       summary?: string | null;
       parameters?: Record<string, any> | null;
       result?: {
         query?: string;
         response?: string;
         answer?: string;
         results?: ToolResultItem[];
         error?: string;
         success?: boolean;
         search_engine?: string;
         total_results?: number;
       } | null;
     }
     ```
   - 在 `ChatResponseMetadata` 中增加 `tool_results?: ToolResultPayload[]` 字段；`ActionStatusResponse` 的 `actions[*].details` 已是 `Record<string, any>`，无需额外调整。

2. **状态存储**  
   - 聊天 store（例如 `web-ui/src/store/chat.ts`）合并返回的 `metadata.tool_results` 到消息记录；异步轮询完成后，将 `steps[*].details.result/summary` 转换成 `ToolResultPayload` 追加到同一条消息的 metadata。

---

## 2. UI 展现

1. **消息渲染**  
   - 在聊天消息组件（`web-ui/src/components/chat/ChatMessage.tsx`）中，若 `message.metadata?.tool_results?.length` > 0，则在助手回复文本下插入 `ToolResultCard` 折叠面板。  
   - 折叠面板标题显示 `summary`，正文罗列 `result.results` 的前三项：`title + source`，可跳转 `url`。若 `results` 为空，展示 `response/answer` 文本。

2. **交互细节**  
   - 默认展开首条，其他条目折叠；支持“查看更多结果”按钮显示剩余列表。  
   - 若 `success === false`，用错误色强调并显示 `result.error`；提供“重试搜索”按钮，再次提交等参数的 `tool_operation`。  
   - 首次渲染时在折叠面板顶部显示提示语：“已调用 Web 搜索获取实时资料”。提示仅在该消息首渲染时出现。

3. **任务详情复用**  
   - Task Drawer（`web-ui/src/components/tasks/TaskDetailDrawer.tsx`）在展示任务信息时，从会话上下文 `recent_tool_results` 中读取最近 N 条搜索摘要，使用同样的 `ToolResultCard` 组件复用 UI。

---

## 3. 异步动作整合

1. **Polling 合并**  
   - `web-ui/src/hooks/useActionPolling.ts` 在动作状态变为 `completed/failed` 时解析 `steps`:  
     ```ts
     const toolSteps = steps.filter(step => step.kind === 'tool_operation');
     const payload = toolSteps.map(step => ({
       name: step.name,
       summary: step.details?.summary,
       parameters: step.parameters,
       result: step.details?.result,
     }));
     ```
   - 将 `payload` 写回消息 metadata（根据 `tracking_id` 定位消息）。该逻辑需兼容已有的执行结果更新。

2. **失败兜底**  
   - 对于 `success === false` 的步骤仍保留卡片，摘要显示“搜索失败”，正文显示错误原因；重试按钮复用聊天输入框发起同样的 `tool_operation`。

---

## 4. 测试与验证

1. **单元/组件测试**  
   - RTL 测试 `ToolResultCard`：  
     - 成功场景显示标题、来源、链接；  
     - 失败场景显示错误消息与按钮。  
   - 聊天消息渲染测试：断言当 metadata 含 `tool_results` 时，折叠组件出现。

2. **集成测试**  
   - Mock `/chat/message` 返回带 `tool_results` 的响应，验证前端 store 合并逻辑和 UI 渲染路径。  
   - Mock `/chat/actions/{id}` 轮询完成后追加 `tool_results`，确认消息更新不会重复插入气泡。

3. **手工验证**  
   - 后端启用真实 `web_search`，在对话中触发一次成功、一次失败调用，检查界面展示和重试按钮行为。

---

## 5. 路线图

1. 第一阶段：完成聊天界面的渲染和轮询合并，确保同步路径可用。  
2. 第二阶段：Task Drawer/计划详情页集成历史搜索摘要。  
3. 第三阶段：统计搜索调用次数、成功率，纳入审计面板（可依赖现有 telemetry 方案）。  
4. 后续可选：为搜索结果支持“添加到任务上下文”按钮，直接写入节点 `context_sections`。
