# Execution and acceptance consistency

This change addresses inconsistencies identified at `d8a386ea` between explicit
task requirements, durable run state, and the currently displayed chat session.

## Acceptance authority

Explicit blocking acceptance criteria and failed hard integrity checks reject
the task even when output files exist. Generated filename heuristics and
explicit nonblocking checks remain advisory. Manual acceptance remains an
intentional override with its existing review record.

Creation-time text inference now stores `source=inferred_text` on both criteria
and contracts. Verification, preflight and artifact resolution preserve that
source. Historical unmarked structured blocks remain explicit; no retrospective
filename-based classification or production-data migration is performed.

Explicit publish contracts must be satisfied by the canonical manifest before a
task can be reused as completed. A successful delegated process does not waive
input or output contracts. The effective-state resolver applies the same rule
to older warning payloads, so resume and downstream dependencies cannot promote
rejected explicit results.

Composite parents continue to aggregate child states. Independent parent
deliverable acceptance is outside this change.

## Session and turn identity

Streaming callbacks are bound to their originating session entity. Changing the
active chat only changes the displayed projection; it does not change the
destination for a running task's messages, plan context, or API updates.

Recovery requires positive current-turn identity through run ID or client
message ID. An arbitrary previous assistant answer is not evidence that the
interrupted request completed. History loads update the owning session and do
not replace another active chat. Optimistic messages remain until their own
persisted turn appears.

Upload-list responses recheck both the originating session and latest request
identity after awaiting the server. A foreign session's plan-created notification
can refresh the plan list but cannot select or bind that plan into the active chat.

## Durable execution lifecycle

A worker must win an atomic lease acquisition before executing. Each execution
attempt has a unique claim identity, including redispatches within one process.
An older worker cannot renew, append events, save assistant output, or commit a
terminal result after another attempt acquires the run.

An owned terminal event records the winning claim. That claim retains authority
to persist its assistant message after lease expiry or release; reaper
interruptions record no winning claim. Lease-loss cancellation stops only after
the terminal database transaction commits, before potentially slow publication.

Terminal status and its final/error event are committed in one transaction.
The reaper performs its stale check and interrupted outcome in the same write
transaction. Task failures are preserved by the run outcome instead of being
converted to success merely because the worker coroutine returned normally.

Fast and durable steering delivery carry the same signal ID. The run queue
applies that ID once; a rejected delivery remains available for retry.

## Dependency order and context compaction

Full-plan execution uses the existing dependency phases, including
parent-after-child synthesis ordering. Display position no longer determines
execution prerequisites. Execution remains sequential; resource-aware parallel
scheduling is a later change.

Compaction retains complete native tool-call/result groups at the recent-history
boundary. The summarizer receives tool names, arguments, and result identities,
including assistant messages with empty text content.

## Validation and follow-up

Regression tests cover explicit rejected outputs, publication authority,
cross-branch producers, source-session final events, correlated recovery,
compaction boundaries, lease ownership, terminal races, and steering deduplication.
CI includes backend chat/plan/artifact contracts and frontend message/recovery
tests in addition to the original unit suite. The frontend job runs the complete
Vitest suite and TypeScript checking. Browser E2E assertions require the current
turn's nonempty completed answer rather than an optimistic message count.

This batch does not introduce a new database schema, distributed workflow
framework, or parallel plan executor. Remaining work includes a unified persisted
output specification, stage-wide deadlines, resumable tool checkpoints, and
artifact-version invalidation.

Memory integration needs a separate per-lane contract: the browser already
queries memory by default and supplies it as chat context, while the native
DeepThink reference-context builder does not currently render that field.
Session-tagged main-store records and per-session-store queries also need a
consistent storage/recall policy before broader memory behavior changes.

The next batch implements the output-specification, shared-deadline and native
checkpoint foundations in `design/2026-10-02-verified-resumable-runs.md`.
