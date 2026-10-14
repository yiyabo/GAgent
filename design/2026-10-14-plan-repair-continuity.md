# Plan repair preserves execution context

Plan #187 task #14 repeatedly read its existing outline fragments, then returned Markdown without creating the declared file. Two repairs consumed their 12-step limits. The executor reused an Agent object but each think call rebuilt messages, losing previous tool calls/results. Chat history is a clipped user/assistant projection and cannot carry native tool receipts. The plan execution intent was also conditional on the disabled budget flag, suppressing existing progress correction. File acceptance accurately rejected the missing output.

Reference: NousResearch/hermes-agent at `0e427d788d253630d38a3a51cd76849e73cd6925` (2026-10-14). `agent/turn_context.py` explicitly seeds from conversation_history; `turn_stop_gates.py` appends verification feedback and continues the same messages. Its code verification policy is not our scientific/file acceptance policy. We adopt transcript continuation while retaining independent OutputSpec verification and publication.

## Implementation

- Native and strict controllers return an internal scoped continuation. Plans feed that transcript into repair and append the exact failed checks; the current system prompt replaces the old one. Prior tool results remain messages, never a dispatch queue. Normal chat has no implicit execution history.
- Scope includes plan/task/session, goal/output contract, and dependency/input binding. Different scopes/protocols, unresolved native calls and orphan results are rejected. Existing context compaction remains responsible for request limits.
- Task-local JSON snapshots under `scratch/.execution/` retain the transcript without duplicating it into API/database bodies. The execution result stores only a versioned path/size/hash/scope reference. Writes use a temporary file, fsync and atomic rename. Loading checks the location, binding and checksum. Corrupt saved history stops before model/tool dispatch and leaves the task failed for reconciliation.
- Legacy failed tasks without a transcript retain the prior candidate response as historical evidence, explicitly not proof that a file was delivered. No synthetic native tool calls are invented. A changed goal or input starts the current task afresh; force_rerun continues to bypass delivery-gap reuse.
- Produced-file evidence survives repairs, including a repair that produces no new files. Files still undergo actual output verification. Internal transcripts cannot satisfy deliverables.
- Execution intent/lane are unconditional for plan tasks; only time budgeting depends on its switch. Plan model calls now honor the same THINKING_ENABLED setting and flat 8000 thinking budget used by chat. No production environment edit or increase in max_tokens/repair attempts/iteration allowances.
- A file target mentioned during repair does not trigger a directory census. Existing directory/dataset checks remain for real directories.
- Task-chain retries reuse completed upstream/subtasks only after current effective-status and file verification; the directly selected task still executes. Reuse is reported separately from executed/skipped work. No model calls are spent on reuse and no plan nodes/instructions are changed.
- Pure native iteration state was moved to a sibling with re-exported controller bindings, keeping the controller below its 2500-line limit and preserving monkeypatch interfaces.

## Validation and limits

Regression cases cover native/strict two-round repair, retaining both a successful write and earlier read receipts, cross-job restore, exactly one initial write, scope/protocol mismatch, unsettled/orphan calls, corrupted snapshots, actual output deletion, transcript exclusion, budget-disabled execution intent, and shared thinking controls. Providers are scripted; real temporary files, controller, code kernel and independent verifier are used. Scientific services and real model APIs are not called.

Existing native/strict, checkpoint resume, cancellation/deadline, plan dependency/publication, prompt policy and SSE tests remain required. Passing deterministic tests does not establish real-model completion-rate or latency improvement. The old #187 failure is not silently accepted, repaired or rerun by deployment.

## Release and rollback

Backend-only change; existing gateway remains. Require full CI, idle gate, consistent DB/config/manifest backup, a candidate app build and smoke/reprobe. No database migration. Roll back source and app image to `1bdabdce`, retain all runtime/history snapshots and user databases. Old binaries ignore the new result-reference field and retain their previous repair behavior; never restore a database over newer user work.
