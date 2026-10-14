# Preserve verification rule errors through the execution and display pipeline

## Observed production failure

Task 16 in Plan 187 completed the input-discovery step and produced an 820947-byte
JSONL file containing 214 parseable records, plus a 7507-byte search strategy.
The job ended with `needs_review_task_ids=[16]`, no failed task IDs. Both the raw
task and its result had `status=needs_review`, with `verification.status=config_error`.

The effective-state resolver called `has_authoritative_verification_failure`.
That method treated any rejected explicit criterion, including a malformed
criterion, as authoritative rejection. It changed the persisted `needs_review`
into `failed` for every page consuming the effective state. Earlier verifier
unit tests had checked the finalization but not its persisted-state projection.

Separately, the DeepThink acceptance guard labeled a malformed check by its type
when no path was in the error response. This injected `glob_count_at_least` into
the model's missing-deliverable list. The resulting synthesis described a check
type as an unproduced file.

## Behavior

- Config-only errors are not evidence of output rejection. Preserve the
  verifier's `needs_review` state across persistence/reload/effective status.
- Real missing files and content failures remain authoritative when declared
  blocking. Mixed config and real failures still reject outputs.
- Keep configuration diagnostics separately from the loop's missing outputs.
  Invalid rules cannot trigger a delivery extension or a request to generate
  a file named after a validator. The final verifier still requires rule review;
  excluding invalid checks from the missing-file loop does not imply acceptance.
- The task result UI displays a warning with the actual configuration error and
  retained-output explanation. It does not suggest rerunning the work to repair
  configuration. Existing genuine failure summaries are unchanged.

No task instructions, task trees, output contracts, aliases, thresholds, raw
execution records or files are rewritten. The production literal JSONL-path
rule with min_count=80 remains unresolved. Its intended interpretation is not
specified by the task instruction; it is not silently converted into a record
count rule. Scientific quality and the model's report limitations remain
separate from JSONL parseability.

## Validation and release

Regression tests exercise finalization -> JSON serialization -> reload ->
effective state, missing-output mixtures, loop diagnostics and result rendering.
Read-only replay of actual production rows and files proves the status change
without launching research or invoking a model. Full backend/frontend CI and
builds are required before deployment. The previous paid evaluation allowance
is exhausted; this change performs zero new model calls.

Deploy through the usual idle gate and backed-up image switch. Existing records
pick up the correct effective state on refresh, without a data migration.
Rollback is the prior application/gateway image; preserve output files and new
user work rather than restoring an old database.
