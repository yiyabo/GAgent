# Production-path evaluation, runtime budgets and versioned research

The implementation starts from 73d9f7f1. Native argument checks, request context
budgets/disclosure, and immutable artifact publication are opt-in until the live
comparison meets its gates. Existing v2 stores continue using version-aware readers.

## Evaluation

The neutral evaluation engine prepares case-specific files under PathRouter's
session input directory and resolves task output directories from the real tree.
It loads either exact app revision through target-root. Private oracles run in the
parent only after execution. Plan lanes invoke PlanExecutor.execute_task; the chat
lane is controller-level, not HTTP/browser coverage. The publisher receives a
trial virtual-project root through its existing constructor dependency.

Every trial has isolated databases/runtime, a terminal run, a released lease and
kernel cleanup. Actual native attempt IDs are observed before requests. External
launches are bounded, and CLI usage reports provider versus estimated counts.
Token thresholds stop before the next trial and are not upstream hard quotas.

The first diagnostic runs exposed setup defects (outside-session inputs, a task
ID incorrectly included in ancestors, publisher runtime/project-root mismatch).
Keep their logs and paid usage. They are excluded from efficacy denominators.
The first d2991dfe paired block also exposed real candidate bugs: empty contract
normalization falsely invalidated task definitions, and promoted external outputs
were verified before their authoritative paths were included. Those candidate
failures remain visible and are not silently replaced by successful reruns.
The remaining five predefined blocks compare 94fc942e app behavior with 0c507e7d
using the corrected neutral engine. Do not pool revisions as one unchanged arm.

## Runtime

AGENT_RUNTIME_V2_ENABLED gates argument validation and core-first monotonic schema
disclosure. Native max_tokens overrides apply only to the selected call; confirmed
length/incomplete-parameter recovery can use 8192 at most twice. Invalid JSON never
executes a handler. Finish reason/usage/diagnostics survive checkpoints.

CHAT_RUN_SYNTHESIS_RESERVE_SECONDS defaults to 0. Candidate value 120 is clamped
to 20 percent of active time, while the original cleanup reserve stays separate.
Soft finalization does not cancel an active mutating tool or extend a deadline.
Native and strict request budgeting includes schemas/framing/output reserve and
preserves deterministic request and execution anchors. Final synthesis falls
back to checked file delivery or explicit missing-output reporting.

## Artifact versions

ARTIFACT_VERSIONING_ENABLED promotes new stores. The plan manifest remains the
authority; content blobs, production-event versions, manifest revisions and UI
projection batches are distinct. Inputs bind before execution. Completion checks
current desired definitions and input versions. Runtime normalization of empty
contracts is not a semantic edit.

Publishing stages immutable data, then commits the manifest within publication
lock -> plan SQL transaction -> main claim fence. SQL/UI receipts can be replayed;
prepared bindings and stale historical results remain available. The coordinator
never claims filesystem and SQLite form a single ACID transaction. Source changes
require explicit user recomputation, not unattended LLM execution. Existing manual
acceptance cannot hide stale inputs. Recompute preview is read-only; enqueueing is
idempotent, scope-bound and reuses the existing execution-job runner.

## Skills

SKILL_RECOMMENDATION_V2_ENABLED uses lexical/semantic RRF (top20 per source, top3
recommendations) over scoped current stable versions, removing newest80 visibility.
Version/hash/model/dimension checks reject stale vectors; foreground embedding
wait is 1.5 seconds with lexical fallback. Background indexing is limited to
8 reservations/hour. Similar methods are grouped, never auto-merged.

SKILL_CONTEXT_PROGRESSIVE_ENABLED provides native indices plus load_skill body
reads. External CLI gets fixed-version files, recorded as external_delivered,
not proof of reading or application. Existing lifecycle/human verification rules
remain. Exposure, body delivery and independent outcomes are separate. Co-used
run costs do not establish a skill's causal savings. The 48-trial natural-query
suite uses curated isolated fixture methods without embedding oracle answers.

## Release evidence

Use explicit flags and recorded config snapshots for candidate evaluation.
Full tests, CI, Python3.10 checks and public smoke precede deployment. A gate that
fails leaves its behavior flag off; report functionality, measured outcomes and
unfinished bounded evaluations separately. Immutable blobs survive rollbacks.
