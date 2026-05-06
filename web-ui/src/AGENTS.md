# FRONTEND SRC

## OVERVIEW
React 18 + TypeScript UI for chat, plans, terminal, artifacts, and DAG visualization. Uses Ant Design 5, Zustand, React Query, Axios, Vite aliases.

## STRUCTURE
```
src/
├── api/          # Axios client, timeout tiers, auth events
├── components/   # Chat, layout, DAG, terminal, markdown/rendering UI
├── hooks/        # React Query and UI hooks
├── pages/        # Route-level screens
├── store/        # Zustand stores and slices
├── types/        # Shared TS interfaces
└── utils/        # Formatting, parsing, rendering utilities
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| API calls | `api/client.ts` | Timeout tiering and auth event handling. |
| Chat state | `store/chat.ts`, `store/slices/message/` | Zustand chat/session/message state. |
| Message loading | `hooks/useMessages.ts` | React Query infinite message loading. |
| Main chat UI | `components/layout/ChatMainArea.tsx`, `components/chat/` | Uploads, streaming display, message rendering. |
| DAG/plan UI | `components/dag/` | Graph visualization and plan interaction. |
| Terminal UI | `components/terminal/` | WebSocket/xterm integration. |

## CONVENTIONS
- Prefer configured aliases: `@components`, `@api`, `@store`, `@hooks`, `@utils`, `@types`.
- TypeScript strictness is relaxed; still avoid widening new code unnecessarily.
- Ant Design is the default component system; preserve existing spacing and interaction patterns.
- Vite dev server runs on port 3001 and proxies `/api` to backend `:9000`, `/ws` to backend WebSocket.
- Vite patches third-party modules (`antd`, `@ant-design/icons`, `rc-input-number`); dependency upgrades can break those patches.

## COMMANDS
```bash
cd web-ui && npm run dev
cd web-ui && npm run lint
cd web-ui && npm run type-check
cd web-ui && npm run test
cd web-ui && npm run test:e2e
```

## ANTI-PATTERNS
- Do not assume strict TypeScript catches all runtime issues.
- Do not remove Vite patch plugins without testing AntD input-number and import analysis.
- Do not point frontend directly at backend URLs in components; use `api/` abstractions.
- Do not watch or import files from `runtime/`, `results/`, `data/`, or `log/`; Vite excludes them to avoid ENOSPC.
