# Plan workspace frontend

Baseline: `7919827770ffa201f4077db41994fb695b748d62`.

The approved design keeps the research conversation beside a plan workspace. The
workspace shows task hierarchy, dependencies, the selected task's execution and
its published artifacts. Plan artifacts and run history remain separate views.

## Layout and compatibility

- Keep Ant Design and the existing API contracts. No new component framework.
- Preserve the original ChatLayout and DAGSidebar as the classic layout.
- Default to the plan workspace; the header layout menu switches back immediately.
- `/chat?layout=classic` overrides the stored preference. Explicit subsequent
  choices update both the preference and the recovery URL.
- Desktop panes support pointer and keyboard resizing and plan focus mode.
  Narrow screens switch between conversation and plan without discarding state.
- Full task details, dependency execution, manual verification/acceptance, Todo
  List, 3D graph, all session files and background tasks remain reachable.
- Only actual task states and completion counts are shown; no estimated percent.

## Scope and evidence

Queries and transient views use session, plan and task identity. Selecting another
plan explicitly changes the target for subsequent conversation. Empty loading
results must not clear plan binding. Task IDs alone are not globally unique.

Task artifacts come from published task receipts and the plan manifest, never by
assigning all conversation files to a selected task. Relative paths need an
explicit source binding or corroborating manifest path. Unknown legacy sources
remain visible without inventing a preview URL. Existing all-files view remains
available. Immutable version history reads the current manifest pointer to label
current versus historical files.

Async task action replies are applied only while their original view still owns
that session/plan/task. An already submitted action may finish on its original
plan; its reply cannot repopulate another plan's task cache.

The persistent log view exposed an existing connection-effect loop: response
metadata/content changed callback identity, repeatedly bootstrapping GET and SSE.
Callbacks now read latest values through refs. Connection generations discard
late old requests, terminal runs stop subscriptions and fallback polling remains
bounded. The original status and control APIs are unchanged.

## Verification and release

Run complete frontend Vitest, TypeScript and production build with both
`VITE_API_BASE_URL=''` and `VITE_WS_BASE_URL=''`. Browser checks use the built UI
with synthetic API fixtures; unknown API and external requests fail closed so
previewing the UI never starts real model calls. Cover plan/session switches with
colliding task IDs, delayed replies, artifact preview/download/history, execution
controls, narrow layouts, resize and classic recovery.

Production backup before this change:
`/data/phage-agent/data/backups/ui-workspace-20261003-131158`.
The full previous dist is archived; its index SHA-256 is
`75c4e454690514e367a5236e5fef0bfb6c2284c30ce1153a8c626939c30dda6d`.
Local rollback tag: `rollback-ui-20261003-79198277`.
The backup contains `rollback_frontend.py`; its independent-directory rehearsal
restored the exact prior index hash.

Release adds the new hashed assets while retaining previous assets for open
browser tabs, then atomically replaces the entry HTML. No backend restart or
database migration is needed. Server rollback runs the saved script and restores
the old entry last. Source and bundle hashes, CI URL and final test counts are
recorded in the release handoff and task outputs.
