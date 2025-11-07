# ToolBox 模块现状与后续规划

记录当前工具箱（`tool_box/`）的实现结构、已接入工具、与聊天系统的交互方式，以及后续待办事项，便于团队在扩展工具时统一约定。

## 1. 当前结构概览

```
tool_box/
  ├─ cache.py                  # ToolCache / PersistentToolCache（内存 + 文件缓存）
  ├─ client.py                 # MCP 客户端封装
  ├─ integration.py            # 对接层，注册内置工具并提供 execute_tool/list_available_tools
  ├─ router.py                 # SmartToolRouter，保留基于 LLM 的自动路由能力
  ├─ server.py                 # MCP Server（未在本项目中启用，可选）
  ├─ tools.py                  # ToolRegistry 定义及注册辅助方法
  └─ tools_impl/               # 实际工具实现
        ├─ __init__.py         # 暴露工具定义（web_search、graph_rag、file_operations、database_query、internal_api）
        ├─ web_search/         # Web 搜索模块（内置 + Perplexity provider）
        ├─ graph_rag/          # 噬菌体 Graph RAG 模块
        ├─ file_operations.py  # 文件读写工具
        ├─ database_query.py   # 数据库查询工具
        └─ internal_api.py     # 内部 API 调用工具
```

- `integration.py` 启动时通过 `register_tool(...)` 将 `tools_impl` 暴露的工具加入 `ToolRegistry`。
- 聊天流程通过 `tool_box.execute_tool(name, **params)` 调用工具。
- 工具返回统一字典结构（`success`, `result`, `error`, ...），聊天管线会使用 `_sanitize_tool_result` 将其写入消息 `metadata.tool_results`。

## 2. 已接入的工具

| 工具名称 | 主要用途 | 参数 | 结果结构 | 备注 |
| --- | --- | --- | --- | --- |
| `web_search` | 模型内置搜索（Qwen/GLM）与 Perplexity 外部搜索 | `query`, `provider`, `max_results` 等 | `response`, `results`, `provider`, `fallback_from`, `success` | 内置失败会尝试 Perplexity 兜底；前端 `ToolResultCard` 已适配。 |
| `graph_rag` | 噬菌体-宿主知识图谱查询 | `query`, `top_k`, `hops`, `return_subgraph`, `focus_entities` | `result` 中含 `prompt`, `triples`, `subgraph`, `metadata` | Triples 数据来自 `tool_box/tools_impl/graph_rag/Triples/all_triples.csv`；支持缓存。 |
| `file_operations` | 文件读取/写入/拷贝/删除 | `operation`, `path`, ... | `success`, `content` 等 | 需注意路径校验。 |
| `database_query` | 数据库查询工具（保留） | 自定义 | 自定义 | 视项目需求保留。 |
| `internal_api` | 调用内部 REST | 自定义 | 自定义 | 保留以备后用。 |

## 3. 与聊天流程的交互

- `StructuredChatAgent._handle_tool_action` 会：
  1. 校验参数（例如 `web_search` 的 provider、`graph_rag` 的 top_k/hops 范围等）；
  2. 调用 `execute_tool(tool_name, **params)`；
  3. 将返回值标准化并写入 `AgentStep.details` 与 `metadata.tool_results`；
  4. 同步追加到 `recent_tool_results`，便于后续 prompt 引用。
- 前端通过 `ToolResultCard` 渲染 `tool_results`：`web_search` 展示引用链接；`graph_rag` 展示三元组/Prompt/子图提示。

## 4. 待办事项

1. **Graph RAG 子图可视化**：前端尚未消费 `subgraph` 字段，可考虑在 DAG 组件或 Modal 中渲染节点/边结构。
2. **工具注册动态化**：当前工具在 `integration.py` 中硬编码注册，后续可加载配置驱动的工具清单（便于扩张）。
3. **Tool Router**：`router.py` 仍保留基于 LLM 的自动工具路由，若未来需要自动鉴别工具，可复用该模块。当前聊天管线未使用。
4. **测试补充**：
   - Graph RAG 缓存命中/兜底逻辑；
   - Web Search 兜底（内置失败 → Perplexity）的集成测试；
   - 工具异常时的 `metadata.tool_results` 行为验证。
5. **MCP Server**：如需对接 MCP 生态，可在 `server.py` 基础上继续集成；目前只使用 `execute_tool` 的直接调用模式。

## 5. 使用注意

- 读写工具涉及文件/数据库操作，务必注意路径与权限；默认允许的目录配置在 `ALLOWED_BASE_PATHS` 中。
- 执行工具前需调用 `initialize_toolbox()`（`tool_box.integration.initialize_toolbox`），确保工具注册完成。
- 工具结果应保持可序列化，避免前端解析异常；若返回自定义类型，请在 `_sanitize_tool_result` 中增加处理分支。
- 需要新增工具时：
  1. 在 `tools_impl/<your_tool>/` 中实现 handler 并暴露工具定义；
  2. 在 `tools_impl/__init__.py` 与 `integration.py` 注册；
  3. 在聊天管线中添加参数校验与摘要逻辑；
  4. 更新前端工具渲染逻辑与相关文档。
