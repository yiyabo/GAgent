# AI Task Orchestration System

AI-driven platform: natural language → executable plans → results.
Multi-agent coordination with tool integration and LLM capabilities.

---

## 1. Quick Commands

```bash
# Full stack (recommended for server deploy or when you want both UI + API at once)
./scripts/start_all.sh
# - Runs scripts/stop_all.sh first, then scripts/sync_skills.sh, then health_check.sh
# - Backend: invokes start_backend.sh (conda activate LLM; see that script). Sets BACKEND_RELOAD=false for stable prod-style runs.
# - Frontend: cd web-ui && npm run dev (port 3001; proxies /api→:9000, /ws→ws://:9000)
# - Logs: log/backend.log, log/frontend.log (PIDs in .pid files alongside)
# - Optional slow service: START_AMEM=true ./scripts/start_all.sh  # also starts A-mem (embedding load)

# Dev: backend only with hot-reload (conda env: LLM, port 9000)
bash start_backend.sh
# Or directly (same venv/interpreter you have active):
python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload --reload-dir app --reload-dir tool_box

# Frontend alone (port 3001, proxies /api→:9000, /ws→ws://:9000)
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
    ├── code_executor.py  #     Code execution via subprocess
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
2. `request_routing.py` classifies **intent** (`intent_type`) and **request tier** (`request_tier`: `light` / `standard` / `research` / `execute`), and sets **`capability_floor`**. The default path grants **`tools`** (full tool-capable DeepThink), not a no-tool “plain chat” mode.
3. `agent.py` builds context (including bound plan/task when applicable) and invokes DeepThink with the routed profile.
4. `deep_think_agent.py` runs the LLM with **native tool calling** (or structured path when configured).
5. Tools executed via `tool_executor.py` → `tool_box/tools_impl/*.py`
6. Response streamed back via SSE

**Explicit task IDs:** If the user message contains numeric task references (e.g. “任务 13、14、15”), routing exposes `explicit_task_ids` / `explicit_task_override`; plan review/optimize heuristics are suppressed for that turn, and `code_executor` resolves the target via `resolve_explicit_task_scope_target` in `guardrail_handlers.py` / `code_executor_helpers.py`. See tests around `test_request_tier_routing` and code_executor guardrails when changing this.

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

### Remote deployment server (GAgent)

- **SSH:** `zczhao@119.147.24.196`
- **仓库目录:** `~/GAgent`（即 `/home/zczhao/GAgent`）
- **网络:** 仅在内网或指定网络下可直连；从本机执行 `ssh` 时需具备网络权限与 **本机已配置的认证方式**（密钥或交互式密码）。**勿将密码写入本仓库、规则文件或 `CLAUDE.md`。**
- **标准更新与重启:** `cd ~/GAgent && git pull && ./scripts/start_all.sh`（脚本行为与 **§1 `./scripts/start_all.sh`** 一致：后端走 conda `LLM`、默认关 reload；遇本地未提交冲突可先对冲突路径 `git stash` 再拉取，见项目部署约定）
- **日志:** `~/GAgent/log/`（如 `backend.log`、`frontend.log`）

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
- `resolve_intent_type()` classifies **intent** (e.g. chat vs research vs `execute_task`) for prompts and policies; it does **not** remove tools by itself.
- `determine_capability_floor()` currently returns **`tools`** unconditionally so the model always **can** call tools (avoids mis-routed “chat” turns with **no** tools and hallucinated answers). `intent_type` is still used for wording and tiering.
- **`request_tier`** is separate: `light` | `standard` | `research` | `execute` (not the string `plain_chat`).
- **Explicit task numbers** in the user message set `explicit_task_ids` / `explicit_task_override` and can pin `code_executor` to a task within the named set; changing routing without tests can break bound execution and plan UX.
- Always run `pytest app/tests/test_request_tier_routing.py -v` after changes.

### phagescope.py — PhageScope API payload format
- API requires BOTH `phageid` (JSON array) AND `phageids` (semicolon-separated)
- `modulelist` must be JSON **object** `{"quality": true}`, NOT array `["quality"]`
- `_build_phage_payload()` handles derivation; don't bypass it

### deep_think_agent.py — Tools and bound tasks
- Bound **execute_task** flows still attach **task context** (instruction, dependencies, artifacts); tool availability in the prompt may be further narrowed by internal toolsets for that mode.
- Legacy **`_SUCCESSOR_TOOLSET` / `plain_chat`**-style mappings may still exist for compatibility; the default chat path uses **`tools`** floor. Do not assume “no tools” for a normal user message.
- Wrong routing or missing task binding can still yield **fabricated** results if tools are not invoked — verify with logs (`tools_used`).

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

## 11. Debugging Protocol（调试规范）

**排查任何异常行为，第一步必须看 log，不要靠猜测。**

### 日志位置

```bash
tail -100 log/backend.log          # 查看最新 100 行
tail -f log/backend.log            # 实时跟踪
grep "ERROR\|error" log/backend.log | tail -50   # 只看错误
grep "phagescope\|POST\|HTTP" log/backend.log | tail -50  # 工具调用记录
```

### 必须结合 log 判断的场景

| 场景 | 要在 log 里确认的内容 |
|------|----------------------|
| AI 给出结果但疑似未调工具 | 是否有对应的 POST 请求记录，`tools_used` 字段是否为空 |
| PhageScope 提交失败/返回错误 | 实际发出的 HTTP payload、response status、error message |
| 意图路由异常（该用工具没用） | `capability_floor`（默认 `tools`）、`request_tier`、`route_reason_codes`、`explicit_task_override` |
| 工具返回结果与预期不符 | 工具实际入参、HTTP 响应原文 |
| 任务状态 Faileds | 服务端 job_id、module_status、module_log.error |

### 关键 log 字段说明

```
capability_floor    → 能力下限；默认路径为 tools（模型可调用工具集）
request_tier        → 请求分级：light / standard / research / execute
tools_used          → 本次实际调用的工具列表（空 = 未调工具）
route_reason_codes  → 路由决策依据，排查意图分类错误的关键
```

### 黄金法则

> **"AI 说它做了"不等于"它真的做了"。永远用 log 验证工具是否被实际调用。**

---

## 12. Glossary (项目术语)

| Term | Description |
|------|-------------|
| **DeepThink** | Extended thinking mode for complex reasoning (扩展思考模式) |
| **capability_floor** | Capability lower bound for tool exposure; default path is **`tools`** (模型始终具备调用工具的能力边界，而非无工具闲聊) |
| **explicit_task_ids** | Parsed from user message when they name task numbers; with **`explicit_task_override`** 可压过 plan review/optimize 启发式并约束 `code_executor` 目标 |
| **PhageScope** | Bacteriophage analysis platform API at phageapi.deepomics.org (噬菌体分析平台) |
| **Skill** | Configurable AI capability module, selected per task (可配置AI技能模块) |
| **Plan** | Task decomposition into dependency DAG (任务分解为有依赖的子任务图) |
| **SSE** | Server-Sent Events for real-time streaming (服务端推送事件) |
| **A-mem** | Agentic memory system for long-term knowledge (智能体长期记忆) |
| **MCP** | Model Context Protocol for tool integration (模型上下文协议) |
| **chat_run** | Single request-response cycle in a chat session (聊天中的单次请求响应周期) |
| **request_tier** | `light` / `standard` / `research` / `execute` — workload / UX tier, separate from `intent_type` (请求分级) |
