# Graph RAG 工具接入方案

本文档描述如何将 `tool_box/tools_impl/graph_rag` 中的噬菌体知识图谱 RAG 模块接入现有对话系统，保持与现有工具（如 `web_search`）一致的封装方式。

## 1. 工具封装与注册
- 在 `tool_box/tools_impl/graph_rag/__init__.py` 定义 `graph_rag_tool` 字典，结构与其他工具保持一致：包含 `name`、`description`、`category`、`parameters_schema`、`handler`、`tags`、`examples`。
- `handler` 内部懒加载 `GraphRAG` 实例（首次构建后缓存），遵循统一返回格式：
  ```python
  return {
      "query": query,
      "success": True,
      "result": {
          "prompt": ...,
          "triples": [...],
          "subgraph": {...} | None,
          "metadata": {...}
      }
  }
  ```
  失败时抛出自定义 `GraphRAGError`，由 handler 捕获并返回 `{"success": False, "error": "...", "code": "...", ...}`。
- 更新 `tool_box/tools_impl/__init__.py` 将 `graph_rag_tool` 加入 `__all__`。
- 在 `tool_box/integration.py::_register_builtin_tools` 中调用 `register_tool(...)` 注册 GraphRAG，保持统一的注册路径。

## 2. 配置与缓存
- 新增 `app/config/rag_config.py`（或扩展既有配置模块），定义:
  ```python
  @dataclass
  class GraphRAGSettings:
      triples_path: str
      cache_ttl: int
      max_top_k: int
      max_hops: int
  ```
  通过环境变量 `GRAPH_RAG_TRIPLES_PATH`、`GRAPH_RAG_CACHE_TTL` 等覆盖默认值。
- handler 使用 `get_rag_settings()` 获取配置；当 `triples_path` 不存在或依赖未安装时给出明确错误。
- 复用 `tool_box/cache.py`：在 handler 调用前计算缓存 key（基于 query/top_k/hops/focus_entities），命中则直接返回；未命中时查询后写入缓存。TTL 使用配置值。

## 3. GraphRAG 服务层
- 在 `tool_box/tools_impl/graph_rag/service.py`（新建）实现 `get_graph_rag_service(settings)`，负责：
  - 加载 CSV 构建 `GraphRAG`。
  - 对外暴露 `query()`，入参与 handler 对齐。
  - 维护模块级单例，避免重复构图。

## 4. 对话管线集成
- `app/services/llm/structured_response.py`：
  - 在 `ToolNameLiteral`/`LLMToolOperation` 枚举中添加 `"graph_rag"`。
  - 更新 `LLMAction` 的参数模型，支持 `graph_rag` 参数：`query`、`top_k`、`hops`、`return_subgraph`、`focus_entities`。
- `app/routers/chat_routes.py`：
  - `_execute_tool_action` 调用 `execute_tool("graph_rag", **params)`，根据结果构建 `ToolResultPayload`，写入 `step.details` 与消息 `metadata.tool_results`。
  - 保持现有 tool_result 流程（失败时记录 error，成功时保留 prompt/三元组）。
  - 若需要自动续写 LLM 回复，可沿用 web_search 的策略：将 prompt 拼回 LLM（或由 LLM 自行处理）。

## 5. 前端展示
- `web-ui/src/utils/toolResults.ts`：识别 `tool_result.name === 'graph_rag'`，保留 `result.triples`、`result.prompt`、`result.subgraph`。
- `web-ui/src/components/chat/ToolResultCard.tsx`：
  - 新增 GraphRAG 特定文案，展示命中三元组列表、prompt、可选的“查看知识图谱”按钮。
  - 若返回 `subgraph`，通过 Modal/Drawer（可重用 DAG 组件）做可视化。
- 如需快捷入口，可在聊天面板添加示例提示（可选）。

## 6. 测试
- `test/tools/test_graph_rag_tool.py`：使用临时 CSV fixture，验证正常查询、top_k/hops 限制、缓存命中、异常处理。
- `test/test_structured_agent_actions.py`：新增 case 模拟 LLM 触发 `graph_rag`，断言 `tool_results` 内容包含 prompt / triples。
- 对 handler 的缓存逻辑、配置加载编写单测，覆盖缺少文件和未安装 `pandas/networkx` 的错误提示。

## 7. 文档与运维
- 更新相关文档（如 `docs/tool_catalog.md`、`docs/LLM_ACTIONS.md`）描述新工具、参数与返回结构。
- 在部署说明中提醒安装依赖：`pandas`, `networkx`。
- 若生产环境需热更新 triples，可在 handler 提供 `invalidate_graph_rag_cache()` 或重载指令。

按照以上步骤实现后，Graph RAG 将以统一的工具封装加入系统，可通过 LLM 工具调用、后端动作执行以及前端 UI 统一展示。***
