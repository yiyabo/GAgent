# Web Search 双通道方案

本文档描述如何在现有系统中同时支持“模型内置搜索能力”(builtin) 与“外部搜索服务”(perplexity/Tavily 等)，并提供统一的调用方式与前后端协作流程。

---

## 1. 设计目标

1. **灵活切换**：默认使用模型自带的联网搜索（如 Qwen/GLM），必要时可切换到第三方搜索（Perplexity 等）。
2. **统一接口**：后端 `web_search` 工具根据 `provider` 参数路由，返回统一结构，便于前端展示。
3. **可扩展**：未来增加更多外部搜索或自研搜索时，只需在 provider 列表中追加。

---

## 2. 配置层

新增 `app/config/search_config.py`：

```python
class SearchSettings(BaseSettings):
    default_provider: str = "builtin"    # builtin | perplexity | ...
    builtin_provider: str = "qwen"       # 或 glm

    # Qwen/GLM 内置搜索的凭证
    qwen_api_key: str | None = None
    qwen_api_url: str | None = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str | None = "qwen-turbo"

    # Perplexity 等外部搜索配置
    perplexity_api_key: str | None = None
    perplexity_api_url: str | None = "https://api.perplexity.ai/chat/completions"
    perplexity_model: str | None = "sonar-pro"
```

`.env` 示例：
```
DEFAULT_WEB_SEARCH_PROVIDER=builtin
BUILTIN_SEARCH_PROVIDER=qwen
QWEN_API_KEY=sk-...
QWEN_MODEL=qwen-turbo
PERPLEXITY_API_KEY=sk-...
```

---

## 3. 工具实现

`tool_box/tools_impl/web_search.py` 重构：

```python
async def web_search_handler(query: str, provider: str | None = None, max_results: int = 5, **kwargs):
    provider = provider or settings.default_provider
    if provider == "builtin":
        return await _search_builtin(query, max_results, kwargs)
    if provider == "perplexity":
        return await _search_perplexity(query, max_results)
    raise ValueError(f"Unknown web search provider: {provider}")
```

统一返回结构：
```json
{
  "query": "...",
  "response": "摘要",
  "results": [
    {"title": "...", "url": "...", "snippet": "...", "source": "..."}
  ],
  "provider": "builtin",
  "success": true,
  "error": null
}
```

### 3.1 内置搜索 `_search_builtin`

- 使用当前默认 LLM (`LLMClient`)，构造带特定 system prompt 的请求，让模型执行联网搜索。
- 若模型接口支持 `enable_search` / `search_parameters` (Qwen 和 GLM 兼容 OpenAI API，可以传这些参数)，优先调用；否则采用结构化 prompt。
- 成功时将模型返回内容整理为 `response` + `results`（可通过解析模型输出中的引用/链接）；失败时返回 `success=False`。

### 3.2 外部搜索 `_search_perplexity`

- 继承现有 Perplexity 实现，发送 REST 请求，解析返回文本/引用列表。
- 失败时返回 `success=False` 并附上 `error` 信息，以便前端提示。
- 保留 `timeout`、重试等现有容错逻辑。

### 3.3 Fallback

- 当 provider=`builtin` 但配置缺失或调用失败时，自动 fallback 到 `perplexity`，同时记录日志。  
  ```python
  try:
      result = await _search_builtin(...)
      if not result["success"]:
          raise RuntimeError("builtin search failed")
      return result
  except Exception:
      logger.warning("Builtin search unavailable, fallback to Perplexity")
      return await _search_perplexity(...)
  ```

---

## 4. Structured Action 与上下文

1. `tool_box/web_search_tool` 的参数 schema 调整：
   ```json
   "properties": {
     "query": {"type": "string"},
     "provider": {
       "type": "string",
       "enum": ["builtin", "perplexity"],
       "default": "builtin"
     },
     "max_results": {"type": "integer", "default": 5}
   }
   ```

2. `StructuredChatAgent`：
   - 若动作参数指定 `provider`，直接透传；
   - 否则先看 `extra_context["default_search_provider"]`（前端 Session 设置）；
   - 再 fallback `settings.default_provider`。
   - Prompt 指南中提示：`web_search` 可选 `provider`，默认 builtin；失败时系统会自动兜底。

3. 会话上下文：
   - 在聊天入口 (`chat_routes`) 把前端传入的 provider 写到 `PlanSession` 的 `extra_context`，支持多轮对话沿用。

---

## 5. 前端改动

1. **UI 设置**：
   - 在聊天面板或设置抽屉添加“默认 Web Search”选择器（内置/Perplexity）。
   - 选择结果存入 chat store，并随消息请求放入 `request.context.default_search_provider`。

2. **消息级覆盖（可选）**：
   - 发送框附近提供快速切换按钮；若切换，则在该条请求 metadata 中附带 `provider`。

3. **展示**：
   - `ToolResultCard` 读取 `payload.provider` 或 `result.provider`，显示“来源：内置搜索/Perplexity”。

---

## 6. 测试

1. 单元测试：
   - mock `LLMClient` 验证 `_search_builtin` 成功/失败路径；
   - mock HTTP 调用验证 `_search_perplexity`；
   - 验证 handler 根据 provider 选择正确方法。

2. Agent 测试：
   - 在 `test/test_structured_agent_actions.py` 添加 `tool_operation: web_search` 带/不带 provider 的场景；
   - 验证 fallback 时 `provider` 字段变更。

3. 示例脚本：
   - 更新 `example/test_web_search_tool.py` 支持 `--provider` 参数：  
     `python example/test_web_search_tool.py --query "AI news" --provider builtin`

---

## 7. 运维指南

1. 确保 `.env` 或配置中心提供 Qwen/GLM 与 Perplexity 的密钥。
2. 默认使用内置搜索，可通过环境变量或前端设置切换。
3. 若检测到 builtin 超时/错误，日志会提示自动 fallback；运维需检查模型配置或网络。

---

## 8. 后续扩展

- 支持更多 provider（`tavily`、`serper` 等）：只需添加 `_search_tavily` 并在 handler 中加分支。
- 支持自研知识库搜索：新建 provider `kb`，在 `_search_kb` 中调用内部服务。
- 将搜索结果缓存入会话上下文（如 `recent_tool_results`）供 Task Drawer 等模块复用。

实施以上方案后，系统即可在保持统一体验的同时，灵活切换模型内置搜索与外部搜索服务。 
