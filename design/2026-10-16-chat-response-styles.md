# Chat reply preferences

The chat composer offers four communication styles (`general`, `product_manager`,
`senior_developer`, `research_advisor`) and three lengths (`auto`, `concise`,
`detailed`). Apply saves the current conversation's settings. The optional
“同时设为新对话默认” checkbox also saves the authenticated owner's defaults.
Existing conversations retain their own settings. Legacy conversations without
settings use general/auto. Explicit instructions in the current user message
about style, language, length and output format take precedence through the
existing model prompt; no phrase classifier or additional model call is added.

## Persistence and request lifecycle

- `GET /chat/response-preferences` reads authenticated owner defaults.
- `PATCH /chat/sessions/{id}` accepts `settings.response_style`,
  `settings.response_length`, and optional `save_response_defaults`.
- Saving both defaults and a conversation uses a short explicit SQLite
  transaction. Both preferences are required for saving defaults.
- New server sessions copy defaults into their existing metadata. The additive
  `user_response_preferences` table stores owner defaults using raw SQL.
- New turns resolve preferences once before serializing a durable chat run.
  `context.chat_response_preferences` includes the two IDs and strategy version.
  Idempotent retries and resume retain that snapshot. Final chat metadata also
  records the snapshot. Changing settings does not alter in-flight turns.
- The same instructions reach structured, simple, native and strict chat paths,
  including final synthesis. Plan workers receive their existing independent
  task context; presets do not change budgets, tool permissions, Plan scope,
  task trees, deterministic receipts or required artifact formats.

## Compatibility and rollback

General/auto preserves the previous professional instruction. Existing response
fields and SSE event ordering remain unchanged. An older compatible app/gateway
can ignore the additive fields and table; preserve the data on rollback rather
than restoring old databases. Reverting the frontend and chat prompt wiring
restores the old UI and expression behavior. This feature does not require
turning on content acceptance or the paused PhageScope Gateway integration.

## Validation scope

Offline tests cover owner isolation, per-session persistence, atomic rollback,
request snapshots/idempotency, native/strict delivery and synthesis, asynchronous
UI updates after switching sessions/accounts, and keyboard apply/cancel.
Browser checks use mocked API responses and no real model or research jobs.
Prompt delivery and regression results do not establish real-model style quality;
that remains for normal user trials or a separately authorized model evaluation.


## Recovered file errors in the same release

Remove the legacy post-processing footer that branded model replies from a
persisted last-error snapshot. Keep the facade hook import-compatible, preserve
raw tool/run failures, and let the current model loop report unresolved work.
Stop loading/persisting last_failure_state and last_evidence_state across user
turns; clean only these derived snapshots on the next session metadata save.
Historical messages and tool receipts are not rewritten.

The chat action bridge stripped native ToolContext before file_operations and
did not rebuild it. Reuse the existing trusted chat context builder after file
parameter normalization for both native and strict callers, so the existing
session-output redirect actually runs. Preserve nested relative output paths
when redirecting the legacy repository results directory. Keep read access and
session isolation checks intact. No blanket permission change, new regex intent
rule, extra classification/review model, or automatic Plan edit is introduced.

Scripted model integration exercises successful writes, failure then recovery,
and genuine unresolved errors through both protocols with the actual file
handler. Final receipts retain failed attempts; recovered final prose stays
unchanged, and a truly unsaved file is still reported as unsaved.
