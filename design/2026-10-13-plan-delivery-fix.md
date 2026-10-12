# Plan delivery and long-run closure release

Base candidate: d5875bde, production base: 9816427b. User authorized implementation and deployment to .8 after verification.

1. `GET /plans/{id}/artifacts`: owner-scoped, maximum 50 records / approximately 90 KiB per page, stable cursor detects changed inventories. SQL extracts publication receipts without transferring thoughts or raw results. Manifest pointers win; file hashing is excluded from GET. Old results API remains compatible.
2. Artifacts UI uses the lightweight list. Failed requests show unknown quantity (or partial loaded count); only a successful empty list shows zero. Pagination, retry, version history on demand, and session switching preserve ownership. Recovered files say file checked / content awaiting review.
3. Historical recovery is a selected, hash-bound operation. Prepare checks task path provenance and file structure without mutation. Apply copies snapshots, CAS-publishes unchecked manifest entries, and atomically changes audited tasks to needs_review. A prepared journal permits crash replay; the original content and execution evidence are retained. No scientific conclusion is certified by file-format checks.
4. Existing background plan budgets and no-progress guards are enabled only after validation. Guard stop reasons flow into task receipts; stopped work without authoritative successful checks becomes needs_review. Hard deadline/cancellation remains failure, and active tools are not blindly replayed. No universal one-hour cutoff is introduced.

Historical #184 review: 55 selected output candidates, 54 pass structural checks, one CSV has headers only and is excluded. Task 7 has no final evidence package, task 11 has no final QA JSON. The manuscript's cited bibliography path is absent from the reviewed output tree. All content remains unchecked; input/intermediate/scratch copies are not promoted as completed deliverables.

Validation: bounded payload and cursor conflicts, current alias ownership, owner isolation, corrupt manifest vs empty state, recovery source/task/manifest conflicts, idempotency and crash replay; frontend loading/failure/pagination/history/scope changes; full backend/frontend and production build; deterministic full journey; finite real-model journey under an explicit allowance. Then CI, backups, idle gate, bundle and image rollout, production HTTP/UI checks and three-host documentation sync. Never force-restart active work.

Rollback: retain old images/config/DB snapshot and pre-recovery session archive. Recovered files are immutable copies; retain them when reverting code. Default-off flags provide behavior rollback; recovery markings remain explicit and are not silently promoted on rollback.
