# 会话级 Web Search Provider 配置方案

## 目标
- 允许前端在聊天界面选择默认的 Web Search Provider（`builtin` 或 `perplexity`）。
- 设置应与会话绑定，影响后续所有 LLM 工具调用；缺省时沿用系统默认值。
- 保持现有工具调用与上下文拼接逻辑的兼容性。

## 后端调整

### 1. 接口层 (`app/routers/chat_routes.py`)
- **响应模型**：在 `ChatSessionResponse` 新增
  ```python
  settings: {
      "default_search_provider": Literal["builtin", "perplexity"] | None
  }
  ```
  若会话未设置，则返回 `null`。
- **PATCH 请求**：扩展 `ChatSessionPatchRequest`，允许提交
  ```json
  {
    "settings": {
      "default_search_provider": "builtin"
    }
  }
  ```
  校验枚举后写入 `session.metadata["default_search_provider"]` 并更新时间戳。
- **持久化**：沿用现有 session repository（若无专门仓库，可直接使用 ORM/DAO）保存 metadata。

### 2. StructuredChatAgent
- 会话加载时读取 `session.metadata.get("default_search_provider")`，写入
  ```python
  agent.extra_context["default_search_provider"] = value
  ```
- 在执行 LLM 返回的 `web_search` 动作时：
  1. 若动作参数指定 `provider`，优先使用。
  2. 否则查 `extra_context["default_search_provider"]`。
  3. 若仍为空，fallback 到 `SearchSettings.default_provider`。

### 3. 工具执行
- 现有 `web_search_handler` 已支持 `provider` 参数，保持不变。
- `StructuredChatAgent` 将最终确定的 `provider` 透传给 `execute_tool("web_search", provider=...)`。

## 前端改动

### 1. 数据层
- 增加 `defaultSearchProvider` 字段到 chat session store。
- 调用 `/chat/sessions/{id}` 获取初始化值；更新时调用 PATCH 接口。

### 2. UI
- 在聊天设置面板或会话信息栏新增下拉选择器（选项：内置搜索 / Perplexity）。
- 选择器修改后立即触发 PATCH 保存，并更新本地状态。
- 发送消息前，将当前 provider 追加到请求上下文（如 `request.settings.default_search_provider`），供后端识别。

### 3. 展示
- 在工具结果卡或任务抽屉中显示 provider 标签，提醒信息来源。
- 若发生 fallback（返回体包含 `fallback_from`），提示用户已自动切换。

## 测试计划

### 后端
1. **Session PATCH**：提交合法 provider，检查 GET 返回值；非法输入返回 422。
2. **Agent 行为**：Mock `execute_tool`，验证在会话设置为 `builtin` 时未显式传参也会以 builtin 调用。
3. **Fallback 记录**：模拟 builtin 失败后自动使用 Perplexity，确认响应包含 `provider="perplexity"` 与 `fallback_from="builtin"`。

### 前端
1. 组件测试：选择器渲染、选择变更时调用 PATCH。
2. 集成测试：发送消息时请求 payload 携带 provider，并在结果卡展示来源。

## 运维说明
- `.env` 中确保配置好 `DEFAULT_WEB_SEARCH_PROVIDER` 与各 provider API key。
- 若用户未设置 session provider，将使用 `DEFAULT_WEB_SEARCH_PROVIDER`。
- 日志中记录 PATCH 操作及 fallback 事件，便于问题排查。
