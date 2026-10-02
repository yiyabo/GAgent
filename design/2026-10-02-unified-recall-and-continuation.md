# Unified recall and explicit continuation

## Problem and result

Chat previously awaited frontend vector retrieval before starting a run; notes then went only to chat prompts, where rendering removed them from the shared context. DeepThink and plan delegates did not consistently receive the same evidence. Project history had no shared original-message recall path. Durable continuation existed only as a backend POST.

The server now resolves one bounded recall envelope before constructing the agent. Plain/structured chat, native/strict DeepThink, plan task prompts and plan decomposition share the same formatter. Context rendering no longer consumes notes. The frontend sends `memory_enabled` instead of making a separate embedding query, and displays admitted source notes and original messages alongside the answer.

## Scope and evidence

- Derive owner/project from persisted chat session, not from recalled text.
- Auto-saved main-store notes use existing `session:<id>` tags. Current session and same owner/project sessions are eligible. Projectless sessions share no unrelated history.
- Untagged user notes without ambiguous task linkage remain user facts. Two high/critical user facts have reserved space among at most five notes.
- Read existing current-session legacy SQLite stores in read-only mode; don't create stores during recall. Duplicate content is admitted once.
- Explicit history language triggers lexical retrieval of original messages; include the immediately adjacent corresponding answer/request without crossing a new user turn. References retain message ID, session, timestamp and recorded status.
- Scope predicates precede candidate limits. English tokens and Chinese bigrams use bounded lexical matching; this is not semantic equivalence to embedding retrieval. Existing vector memory endpoints remain available.
- Final evidence envelope is at most 6500 characters. Past answers are historical claims, not proof of current execution or file existence.
- Closing the memory switch disables automatic recall, including history recall. It preserves existing automatic memory saving behavior.
- `GET /chat/sessions/{session_id}/recall?q=...` exposes explicit scoped lookup. It returns excerpts, not complete archived conversations.

## Continuation

- `GET /chat/runs/{run_id}/resume?session_id=...` reports capability without claiming a run or exposing request credentials.
- Failed/cancelled durable-run messages show “继续此任务”. Clicking preflights the original session, then uses the existing POST resume endpoint with a fresh client-message ID and streams the new child run.
- Preserve the old terminal run. Completed ledger steps are validated before reuse; this does not guarantee arbitrary external operations can resume.
- Reject missing/damaged checkpoints and uncertain writes in the UI with explicit reasons. Old tasks and unsupported delegated scopes may still require a new request.
- A session switch during preflight aborts dispatch. After dispatch, stream callbacks retain their source session. Processing fences and a component pending guard prevent concurrent clicks from creating duplicate children.
- Preserve the unsent composer draft and do not attach newly selected files to a resumed historical request.

## Remaining phases

Verified workflow-to-skill learning, common agent-core consolidation, richer output contracts and artifact versions remain separate phases. Recall currently searches stored lexical evidence; it has no new FTS migration, learned summarizer, or automatic correction of old notes.
