# PROJECT KNOWLEDGE BASE

**Generated:** 2026-05-06 23:38:41 CST
**Commit:** 1955f57
**Branch:** fix/phagescope-plan-execution

## OVERVIEW
AI task orchestration platform: natural language requests become plans, tool calls, artifacts, and streamed chat responses. Core stack is FastAPI + SQLite + multi-provider LLM tooling, with React/Vite frontend and a separate tool ecosystem.

## STRUCTURE
```
Phage-Agent/
├── app/                    # FastAPI backend: routers -> services -> repository -> SQLite
├── tool_box/               # Declarative tool registry and concrete tool implementations
├── web-ui/                 # React 18 + Vite UI on port 3001, proxies API/WS to backend
├── scripts/                # Restart-safe full-stack orchestration; no Makefile/CI wrapper
├── skills/                 # Project skills synced to ~/.claude/skills before startup
├── data/, runtime/, results/, log/  # Local inputs, run artifacts, outputs, logs; do not document as source modules
└── CLAUDE.md               # Full operational contract and danger zones
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Backend startup | `app/main.py` | Lifespan initializes DB, LLM clients, realtime bus, toolbox, stale jobs. |
| Route registration | `app/routers/__init__.py` | Imports default route modules; new routers register via registry. |
| Chat flow | `app/routers/chat/` | Routing, guardrails, action handling, DeepThink handoff. |
| DeepThink engine | `app/services/deep_think_agent.py` | Native tool calling, strict protocol, evidence barriers. |
| Plan execution | `app/services/plans/` | DAG execution, dependency validation, artifacts, verification. |
| Tool execution bridge | `app/services/execution/tool_executor.py` | Timeouts, ToolContext injection, deliverable publishing. |
| Tool definitions | `tool_box/tool_registry.py` | `_STANDARD_TOOLS`, orchestration metadata, registration. |
| Tool handlers | `tool_box/tools_impl/` | Large stateful implementations: code executor, PhageScope, writing. |
| Frontend | `web-ui/src/` | API client, Zustand stores, AntD chat/plan/terminal UI. |
| Runtime logs | `log/backend.log` | First stop for abnormal behavior; verify `tools_used`, routing fields. |

## CODE MAP
LSP servers are not installed in this workspace (`basedpyright-langserver`, `typescript-language-server` missing). Use file-level map:

| Area | Entry | Role |
|------|-------|------|
| App factory | `app/main.py` | Creates `FastAPI`, registers routers, startup cleanup/resume. |
| Request routing | `app/routers/chat/request_routing.py` | Binary `intent_type`, request tier, explicit task parsing. |
| Chat agent | `app/routers/chat/agent.py` | Builds context and invokes DeepThink. |
| Plan executor | `app/services/plans/plan_executor.py` | Executes DAG tasks and blocks on dependency/artifact gaps. |
| Artifact contracts | `app/services/plans/artifact_contracts.py` | Alias canonicalization, manifest paths, publish/resolve helpers. |
| Verification | `app/services/plans/task_verification.py` | Acceptance checks and artifact authority demotion. |
| Settings | `app/services/foundation/settings.py` | Central env/config contract for LLM, DB, embeddings, auth, plans, skills. |
| Database pool | `app/database_pool.py` | SQLite connection pooling and `get_db()` context manager for repositories. |
| Prompts | `app/prompts/__init__.py` | Central prompt template manager for DeepThink/system prompts. |
| Tool registry | `tool_box/tool_registry.py` | Tool list + read-only/concurrency/destructive metadata. |
| Tool cache | `tool_box/cache.py` | In-memory/persistent TTL cache and tool result stats. |
| Frontend entry | `web-ui/src/main.tsx` or `web-ui/src/index.tsx` | React mount depending on current branch layout. |

## CONVENTIONS
- Use `python`, not `python3`, for local commands unless an existing script does otherwise.
- Restart backend/frontend only with `./scripts/start_all.sh`; it stops services, syncs skills, checks Docker image, starts backend/frontend, runs health checks.
- Backend settings come from `app/services/foundation/settings.py`; prefer `get_settings()` over scattered `os.getenv` in services.
- Raw SQL repositories, no ORM. SQLite plan DBs also live under `data/databases/plans/`.
- All request handling has full tool access; routing changes must preserve `intent_type` and `request_tier` semantics.
- Frontend aliases: `@components`, `@pages`, `@hooks`, `@utils`, `@types`, `@api`, `@store`.
- Frontend TypeScript is intentionally relaxed (`strict=false`, `noImplicitAny=false`); do not infer strict-mode guarantees.

## ANTI-PATTERNS (THIS PROJECT)
- Do not bypass `scripts/start_all.sh` for restarts.
- Do not commit `.env` or credentials; `.env` contains provider keys.
- Do not call real PhageScope API from tests; mock with `monkeypatch`.
- Do not bypass `_build_phage_payload()`; PhageScope requires both `phageid` array and `phageids` semicolon string, and `modulelist` object form.
- Do not trust an AI response that claims tool work; verify `tools_used` and logs.
- Do not treat `runtime/`, `results/`, `data/`, `log/`, `node_modules/`, or generated task outputs as source.

## UNIQUE STYLES
- Chat routing is intentionally binary (`chat` vs `execute_task`); depth is controlled separately by `request_tier`.
- Plan execution uses canonical artifact manifests as authority; local file existence alone is not enough for downstream dependencies.
- Tool orchestration metadata (`is_read_only`, `is_concurrent_safe`, `is_destructive`) affects DeepThink behavior.
- Skills are project artifacts but synced into user-level `~/.claude/skills/` before service start.
- Vite patches third-party modules at build/dev time for AntD/rc-input-number quirks.

## COMMANDS
```bash
./scripts/start_all.sh
bash start_backend.sh
cd web-ui && npm install && npm run dev
pytest app/tests/ -v
pytest -m "not external" -v
pytest app/tests/chat/test_request_tier_routing.py -v
cd web-ui && npm run lint && npm run type-check && npm run test
```

## NOTES
- For GitHub push from this environment, set proxy first as documented in `CLAUDE.md`.
- `START_AMEM=true ./scripts/start_all.sh` starts the slow optional A-mem service.
- `FRONTEND_NODE_BIN_DIR` / `FRONTEND_NODE_VERSION` may pin Node 16 on deployment hosts.
- Debugging rule: first inspect `log/backend.log`; key fields are `intent_type`, `request_tier`, `tools_used`, `route_reason_codes`.
