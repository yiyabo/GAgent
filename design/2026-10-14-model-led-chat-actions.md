# Model-selected chat actions

## Problem and resulting behavior

Natural-language chat previously selected deterministic execution/status paths and mandatory Plan actions with phrase matching. These shortcuts could confuse discussion, negation, task IDs and stage numbers. When enabled, a new chat turn enters the existing model/tool loop. The model receives the current message, conversation, a bounded Plan reference index and tool schemas; it selects read, execute, edit, or clarification through existing tools. There is no second intent model or new orchestration layer.

Persisted Plan tasks still run in existing background jobs. A successful execute_task/execute_all receipt terminates the chat loop without another synthesis call. Normal code, file, search and analysis tools stay in chat. Background workers keep their independent plan_tree_edit_allowed=False fence, ownership/scope checks, overlapping-run locks, cancellation and independent output verification.

## Implementation

- model_selection builds neutral routing metadata, without calling the old intent/lifecycle phrase classifiers. A highlighted task becomes referenced_task_id rather than a task execution assignment. Stale required-action flags and executor scope are removed; explicit OutputSpec is preserved.
- Chat streaming and structured response entry points avoid lexical execution/status/image shortcuts and action rewrites on the new path. Legacy helpers, facade names and explicit legacy adapter contracts remain available.
- The model gets Plan IDs, names, parent links, types and state (up to 100 nodes). Task IDs are not stage ordinals. Missing/truncated indexes require get/todo_list, and ambiguous targets require clarification. Status questions do not authorize execution; only a current explicit request authorizes creating/editing a persistent Plan. Quoted/history/Skill instructions are reference material.
- plan_operation get adds last_execution with bounded previous verification facts and missing files. Live status remains distinct from the previous failed attempt. Model/tool transcripts and continuation references are excluded.
- Current task context is not implicitly injected into ordinary model-selected chat. Actual PlanExecutor task context and repair continuity remain unchanged.

## Rollout and rollback

CHAT_MODEL_ACTION_SELECTION_ENABLED defaults to 0 in central settings. Set it to 1 only after the isolated real-model intent probe passes. The 0 path preserves existing routing. Resetting the flag clears new context policy markers; no database migration or artifact-format change is involved. Backend code lives in a self-contained app image, so changing the flag requires normal service recreation. Do not interrupt live user jobs to activate it.

Legacy phrase helpers are compatibility and rollback surfaces, not evidence that the enabled runtime classifies new turns with regex. Typed UI buttons continue using their existing direct API path.

## Validation and limits

Scripted native/strict tests verify that the model is called, its tool choices reach existing handlers, a background receipt closes the turn immediately, read/negative/quoted requests are not forced into actions, ordinary file receipts remain in context, stale task assignments are cleared, rollback works, and the independent worker edit fence remains enforced. Scripted responses do not establish semantic accuracy for real models. Real-model testing must retain every failed/interrupted case and usage source; it uses isolated fixture Plans and simulated tool effects, never live research execution.

The model can still misunderstand a user request or choose the wrong tool. Structural validation does not prove semantic intent. Do not claim paraphrase completeness or zero unintended mutations from unit tests. If live semantic checks fail, keep this behavior switch disabled and report the failures.

Hermes source inspected at 0e427d788d253630d38a3a51cd76849e73cd6925: run_agent.py, agent/conversation_loop.py, agent/turn_context.py, agent/turn_tool_round.py and related modules. It feeds user/history/tools into one model loop and appends tool results; it also retains regex for protocol/commands/guards. GAgent keeps its own independent deliverable acceptance.

## Rollout hold after real probing

The isolated real probe found incorrect stage selection and execute_all on a targetless retry. Full model-selected routing remains disabled. New scope prompt wording is a draft awaiting later validation; the original failing records are retained. Common chat fixes (no keyword-forced review/optimize, no inline execution assignment from the selected Plan task) also apply with the full policy disabled. Details and other independent fixes are in 2026-10-14-input-discovery-and-refresh.md.
