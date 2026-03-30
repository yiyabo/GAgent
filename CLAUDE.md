# AI Task Orchestration System

AI-driven platform: natural language → executable plans → results.
Multi-agent coordination with tool integration and LLM capabilities.

---

## 1. Quick Commands

```bash
# Backend (conda env: LLM, port 9000, auto-reload)
bash start_backend.sh
# Or directly:
python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload --reload-dir app --reload-dir tool_box

# Frontend (port 3001, proxies /api→:9000, /ws→ws://:9000)
cd web-ui && npm install && npm run dev

# Tests
pytest app/tests/ -v                        # All backend tests
pytest app/tests/test_xxx.py -v             # Single file
pytest -m "not external" -v                 # Skip external services
pytest -k "phagescope or routing"           # By keyword
cd web-ui && npm run test                   # Frontend tests

# Code quality
cd web-ui && npm run lint && npm run type-check
```

---

## 2. Project Layout

```
app/                    # FastAPI backend
├── main.py             #   Entry point + lifespan
├── routers/            #   API routes (chat/, plans, artifacts, auth)
│   └── chat/           #     Chat flow: routes→request_routing→agent→deep_think_agent
├── services/           #   Business logic
│   ├── foundation/     #     Settings (Pydantic), logging, request principal
│   ├── deep_think_agent.py  # Extended thinking with tool calling (核心推理引擎)
│   ├── plans/          #     Plan executor, task graph, DAG
│   ├── execution/      #     Tool executor, context injection
│   ├── embeddings/     #     Qwen/GLM embeddings + vector cache
│   ├── skills/         #     Skill discovery + selection
│   └── memory/         #     Chat memory + agentic memory (A-mem)
├── repository/         #   Raw SQL data access (no ORM)
├── prompts/            #   System prompts & templates
├── tests/              #   pytest test suite
└── database_pool.py    #   SQLite connection pool

tool_box/               # Tool ecosystem
├── tool_registry.py    #   Declarative tool definitions (_STANDARD_TOOLS, _CUSTOM_TOOLS)
├── tools.py            #   Registration API (register_all_tools at startup)
├── router.py           #   Tool selection logic
├── cache.py            #   LRU + persistent result cache
└── tools_impl/         #   Tool implementations
    ├── phagescope.py   #     Bacteriophage analysis (噬菌体分析)
    ├── claude_code.py  #     Code execution via subprocess
    ├── terminal_session.py  # SSH with approval flow
    ├── manuscript_writer.py # Scientific writing
    └── ...             #     20+ tools total

web-ui/                 # React + TypeScript frontend
├── src/
│   ├── api/            #   Axios client with timeout tiering
│   ├── components/     #   Chat, Plan, Terminal, Graph UI
│   ├── store/          #   Zustand state management
│   ├── pages/          #   Route pages
│   └── types/          #   TypeScript interfaces
├── vite.config.ts      #   Dev server + API proxy config
└── package.json        #   React 18, Ant Design 5, Monaco, xterm.js
```

---

## 3. Architecture

```
Router (app/routers/)  →  Service (app/services/)  →  Repository (app/repository/)  →  SQLite
```

### Chat request flow (最常用路径)
1. `routes.py` receives message → creates `chat_run`
2. `request_routing.py` classifies intent → determines `capability_floor`
3. `agent.py` selects tools based on capability_floor
4. `deep_think_agent.py` runs LLM with native tool calling
5. Tools executed via `tool_executor.py` → `tool_box/tools_impl/*.py`
6. Response streamed back via SSE

### Plan execution flow
1. `plan_operation` tool decomposes goal → task DAG
2. `plan_executor.py` executes tasks in dependency order
3. Each task may invoke tools, produce artifacts
4. Results assembled and returned

### Tool invocation flow
```
Tool Router (selects tools) → Tool Executor → Cache check → Handler → Cache store → Return
```

---

## 4. Adding a New Tool

1. Create `tool_box/tools_impl/my_tool.py` with async handler function
2. Register in `tool_box/tool_registry.py` under `_STANDARD_TOOLS` or `_CUSTOM_TOOLS`
3. Add tests in `app/tests/test_my_tool.py`
4. Restart backend — auto-discovered at startup

Tool categories: `information_retrieval`, `document_writing`, `file_management`,
`data_access`, `system_integration`, `document_processing`, `vision`,
`paper_replication`, `knowledge_graph`, `bioinformatics`, `execution`,
`analysis`, `planning`, `deliverables`

---

## 5. Adding Routes / Services

**New route:**
1. Create `app/routers/my_routes.py` with FastAPI decorators
2. Register in `app/routers/__init__.py` via `register_router()`

**New service:**
1. Create `app/services/my_service/` module
2. Use `get_settings()` for config injection
3. Call from routers or other services

---

## 6. Tech Conventions

- **Database:** Raw SQL with SQLite, connection pool (`database_pool.py`), row factory returns dicts
- **LLM client:** `app/llm.py` — multi-provider (Qwen/OpenAI/Claude/Perplexity/Kimi)
- **Streaming:** SSE (Server-Sent Events) for chat, WebSocket for terminal
- **Settings:** Pydantic-based, loaded from `.env`, accessed via `get_settings()` singleton
- **Frontend state:** Zustand stores (`web-ui/src/store/`)
- **UI components:** Ant Design 5
- **Path aliases:** `@components`, `@pages`, `@hooks`, `@utils`, `@types`, `@api`, `@store`
- **Error handling:** Custom exceptions in `app/errors/`, catch specific exceptions
- **Async:** All service calls are async; use `await` consistently

---

## 7. Environment Variables (key groups)

Full list in `app/services/foundation/settings.py`. Key groups:

| Group | Key vars | Notes |
|-------|----------|-------|
| LLM | `LLM_PROVIDER`, `QWEN_API_KEY`, `QWEN_MODEL` | Default: Qwen |
| Embedding | `EMBEDDING_PROVIDER`, `QWEN_EMBEDDING_MODEL` | Default: text-embedding-v4 |
| Backend | `BACKEND_PORT`, `CORS_ORIGINS`, `APP_ENV` | Port 9000 |
| Chat | `CHAT_HISTORY_MAX_MESSAGES`, `DEEP_THINK_MODE` | Max 200 messages |
| Thinking | `THINKING_ENABLED`, `THINKING_BUDGET` | Extended thinking tokens |
| Skills | `ENABLE_SKILLS`, `SKILL_SELECTION_MODE` | hybrid or llm_only |
| Remote | `BIO_TOOLS_EXECUTION_MODE`, `BIO_TOOLS_REMOTE_HOST` | SSH execution |
| Debug | `LOG_LEVEL`, `CTX_DEBUG`, `BUDGET_DEBUG` | Set to DEBUG/true |

---

## 8. Danger Zones (修改前务必了解)

### request_routing.py — Intent classification (意图分类)
- `resolve_intent_type()` determines if message is chat/research/execute
- `determine_capability_floor()` maps intent → tool availability
- **`plain_chat` floor = NO tools = LLM may hallucinate**
- Always run `pytest app/tests/test_request_tier_routing.py -v` after changes

### phagescope.py — PhageScope API payload format
- API requires BOTH `phageid` (JSON array) AND `phageids` (semicolon-separated)
- `modulelist` must be JSON **object** `{"quality": true}`, NOT array `["quality"]`
- `_build_phage_payload()` handles derivation; don't bypass it

### deep_think_agent.py — Capability floor → tool list
- `capability_floor` from request_routing determines which tools agent can use
- `_SUCCESSOR_TOOLSET` maps floor levels to tool sets
- Wrong floor = agent without needed tools = fabricated results

### .env — API keys
- Contains all provider API keys — NEVER commit to git
- `.gitignore` already excludes it

### terminal_session.py — SSH remote execution
- Has approval workflow for dangerous commands
- Do not bypass approval checks

---

## 9. Testing

- **Naming:** `test_<feature>_<scenario>.py` / `def test_<action>_<result>()`
- **Markers:** `@pytest.mark.integration`, `@pytest.mark.prod_smoke`, `@pytest.mark.external`
- **Async:** `@pytest.mark.asyncio` for async test functions
- **PhageScope tests:** Always mock with `monkeypatch`, never call real API
- **Fixtures:** See `app/tests/conftest.py` for shared fixtures
- **Config:** `pytest.ini` — testpaths=`app/tests`, strict markers

---

## 10. Git Conventions

**Commit format:** Conventional Commits
```
feat(scope): description        # New feature
fix(scope): description         # Bug fix
refactor(scope): description    # Code restructuring
test(scope): description        # Test additions
docs(scope): description        # Documentation
```

**Recent examples:**
```
feat(manuscript): add Nature exemplar style hints
fix(claude_code): scrub CLAUDE_* env in api mode
fix: add paste/drag-drop upload to ChatMainArea
```

**Branches:** `feature/xxx`, `bugfix/xxx`, main branch: `main`

---

## 11. Glossary (项目术语)

| Term | Description |
|------|-------------|
| **DeepThink** | Extended thinking mode for complex reasoning (扩展思考模式) |
| **capability_floor** | Minimum capability level determining available tools (能力下限，决定可用工具集) |
| **PhageScope** | Bacteriophage analysis platform API at phageapi.deepomics.org (噬菌体分析平台) |
| **Skill** | Configurable AI capability module, selected per task (可配置AI技能模块) |
| **Plan** | Task decomposition into dependency DAG (任务分解为有依赖的子任务图) |
| **SSE** | Server-Sent Events for real-time streaming (服务端推送事件) |
| **A-mem** | Agentic memory system for long-term knowledge (智能体长期记忆) |
| **MCP** | Model Context Protocol for tool integration (模型上下文协议) |
| **chat_run** | Single request-response cycle in a chat session (聊天中的单次请求响应周期) |
| **request_tier** | Classification level: plain_chat / research / execute_task (请求分级) |
