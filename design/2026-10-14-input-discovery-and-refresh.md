# Inspect unresolved inputs and preserve interrupted chat turns

The production incident combined three defects. Task 15 published `phage_depolymerase.review_outline_md`; Task 16 requested `phage_depoly.review_outline_md`. The registered alias mismatch stopped the execution request despite a real 51,514-byte outline. The user's quoted error also triggered the old `review` keyword route. A synchronous rubric model call blocked the event loop for roughly 80 seconds, the 30-second lease expired, and automatic checkpoint resumes persisted the same user question again. The container had not restarted; its stale-run message incorrectly claimed it had.

## Input confirmation in the existing Agent loop

Missing-producer preflight findings become warnings while `PLAN_INPUT_DISCOVERY_ENABLED` is enabled (default true). Structural cycles, ambiguous exact producers and malformed contracts still block. The worker inventories existing validated publications and verified declared outputs within the same Plan. A missing exact registration does not select a replacement or claim an input is present.

For native/strict task execution with candidates, the existing task Agent first reads the actual materials. `plan_operation resolve_input` accepts a candidate ID, required alias and reason only after a successful read and byte/provenance recheck. Until all pending inputs are confirmed, searches, computation and output writes are rejected as `executed=false`. No extra intent model, semantic-matching API call, Agent or scheduler is created. Empty inventories and unsupported delegation paths remain explicit blockers.

The per-execution record contains the requested alias, source producer/version/path/hash, current task definition and explanation. It is separate from task instructions, output contracts and tree structure. Reuse checks the actual bytes and published source pointer; changed inputs invalidate old confirmation. v2 runs snapshot selected sources through the existing immutable store and publication fence. Input-confirmation records are excluded from output-path harvesting and never satisfy a task's output requirement. A final answer with unresolved inputs cannot publish a successful result.

## Review, refresh and continuation

Rubric evaluation runs via `asyncio.to_thread`, preserving context while heartbeat, SSE and control callbacks remain schedulable. The active-run fence is checked before saving the review. Lease expiration is reported as heartbeat interruption, not an asserted server restart.

New continuations reuse the original user-message ID. History projects proven legacy continuation copies into one visible question, retaining database rows and original run logs. Its continuation action points at the latest failed attempt and shows the interruption reason. A resumed attempt that fails again is not automatically resumed again; deliberate manual continuation remains available. Cancellation does not silently restart work.

The execution modal distinguishes an unsuccessful execution request from task execution results. It reports that prerequisite task states are satisfied while input files still need checking; it no longer claims every prerequisite is satisfied beside an input preflight error.

## Invalid acceptance rules

The captured Task 16 also declares `glob_count_at_least=80` against one literal JSONL filename. This counts files, not records. The checker reports an invalid rule rather than 79 missing files. When real output evidence exists and only invalid verification rules remain, the result is `needs_review`, with output retained and the configuration error visible. It is not accepted as completed and does not trigger futile contract-repair loops. The existing task's requested count and criteria are not silently rewritten.

## Validation / release boundaries

Deterministic native/strict tests cover read → confirm → write in the same loop, source changes, wrong scope, replayed receipts, immutable snapshots, current manifest changes, empty inventories and confirmation not counting as output. Additional checks cover event-loop responsiveness during review, one user message per continued turn, legacy history projection and UI error display. Production files are copied into an isolated offline fixture; the captured outline can be bound without changing Task 16's contract. This is pipeline evidence, not a real-provider semantic accuracy measurement.

Natural-language action selection remains separately controlled by `CHAT_MODEL_ACTION_SELECTION_ENABLED`; its real-model quota decision is still pending. Both settings permit behavior rollback; existing recorded input bindings stay readable. No production task is restarted, manually accepted, renamed or otherwise rewritten by these changes.
