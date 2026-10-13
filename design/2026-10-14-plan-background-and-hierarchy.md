# Plan task submission and recursive hierarchy

The product boundary is explicit: execution of tasks already in a Plan tree goes
to plan background jobs. Ordinary chat searches, code, file operations and
analysis continue in chat. A status question does not authorize execution.
Execution and retry do not authorize editing the persisted task tree. Plan
creation determines the hierarchy up front; later edits/decomposition require
an explicit user instruction or an explicit UI edit action. An Agent's internal
todo-list is only a work checklist and must not create Plan nodes.

## Problems observed

- “重新执行吧” followed a Task 4 status reply but the session still held Task 3.
  The old shortcut required a numbered task; the turn entered chat DeepThink
  and worked on the older binding instead.
- Explicit multi-task requests and structured `rerun_task` / `execute_plan`
  actions still waited inside the chat worker.
- Plan #185 retained composite seed metadata, but all recursive decomposition
  calls failed with empty/incomplete JSON. No children were created; the UI
  inferred every childless stage was atomic. Its `llm_calls=0` counted only
  successfully parsed responses and concealed the attempted calls.
- One node's `should_stop` stopped the entire BFS queue. A separate seed-contract
  shortcut treated declared deliverables as proof that a task was already atomic.

## Implementation

1. Resolve execution targets before chat tools. Explicit task IDs win; short
   followups use the most recent clear conversational task before an older
   session binding. Missing or ambiguous retry targets request a selection.
2. Reuse the task panel's async plan routes for single tasks, selected scopes
   and whole plans. Selected scopes share one job, union their dependencies,
   follow topological order and reserve every task before creating a job row.
   Full-plan and individual-task locks exclude overlapping execution.
3. Structured chat actions and `plan_operation` use the same submission adapter.
   `execute_task` accepts `target_task_ids`. Chat returns the job receipt and
   does not relay the plan worker's thinking into the conversation. A model-led
   submission also terminates the native/strict chat loop after its tool receipt.
4. Preserve root/composite/atomic declarations. Explicit atomic seeds may skip
   automatic decomposition; composite stages with delivery contracts still split
   while retaining the parent's method and deliverables. A branch stop leaves
   other BFS branches runnable. Failed stages remain composite. A childless
   stage can execute its original instructions and output contract; a failed
   decomposition is a warning, never a demand to split the task before retrying.
5. Structured decomposition uses `DECOMP_MAX_TOKENS=8192` and
   `DECOMP_THINKING_ENABLED=0` by default, with one format retry under the existing
   `DECOMP_RETRY_LIMIT=1`. Invalid JSON is regenerated, never patched into guessed
   children. The synchronous stream forwards the thinking policy to the provider.
6. The UI retains unfinished composite types and exposes a partial-decomposition
   notice with a separate "继续拆解" action on the selected childless stage. This
   submits the existing asynchronous decomposition API and shows its own job
   log; it does not execute the research task. Submission callbacks are scoped
   to the initiating session/plan/task, and queued runs disable repeat clicks.
   Job-board decomposition records retain their target task so reopening the
   workspace still shows the associated run. Historical #185 files/results are retained; displaying that its
   hierarchy is incomplete does not fabricate missing child tasks.
7. The server supplies a plan-edit flag derived from the current user turn.
   Execution workers always disable it, independently of budget feature flags.
   The existing `plan_operation` handler blocks mutations before dispatch;
   native/strict/code-mode RPC preserve that context, and structured task/plan
   actions enforce the same rule. Read/get/todo-list remain available. Review
   scores the existing tree; explicit changes apply only those changes without
   running generation first. Added tasks retain their supplied output metadata.
   Replay of an existing creation receipt remains compatible and writes no new
   tasks. No new scheduler, intent category or LLM permission classifier is added.

## Verification and release

Deterministic tests cover the exact followup with stale Task 3 context, scope
deduplication, delayed background workers, overlapping submissions, ordinary
chat remaining inline, native/strict immediate handoff, malformed JSON retry,
contract-preserving recursive hierarchy, branch stops and preflight rejection of
failed decomposition as a warning, unauthorized mutations with zero handler
dispatch, RPC propagation, explicit edits and metadata preservation. External
scientific services and models are mocked.

No new paid-model trial is authorized in this work. Existing production tasks
are not automatically restructured or rerun. Release requires full CI and an
idle restart gate; old app/gateway images, databases, manifests and configuration
are preserved. There is no database schema migration. Rollback aligns code and
images and retains user data created after deployment.

The user chose to retain the current #185 run and its already-added #8–14 tasks.
Do not cancel that run, remove those nodes, or deploy through a busy gate to
activate the correction. Prepare the corrected release and publish when idle.
