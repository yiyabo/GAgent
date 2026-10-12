# Runtime closure validation — 2026-10-12

## Scope and release decision

Base: `9816427b`. Initial candidate: `6563c577`. The implementation plan is
`design/2026-10-12-runtime-closure-plan.md`. This report distinguishes tests from
real-model evidence. Production remains on the base release; new plan budgets
and required output contracts remain disabled pending another approved real run.

## Real journey (one attempt, retained failure)

The user authorized an additional 300,000-token **post-response admission
threshold** and 1,200 seconds. This allowance is separate from the October 3
campaign. No failed model phase was automatically rerun.

The supervisor held the oracle outside the Agent runtime and exercised real
HTTP/SSE, uploaded a synthetic CSV, requested diagnosis, then requested a root
and three dependent tasks (clean, aggregate, report). Model endpoints were real.
The environment used isolated databases and macOS child kernels; this is not a
production Linux sandbox equivalence test.

| Measurement | Result |
|---|---|
| Evaluated code | `6563c577` (source archive retained) |
| Requested normal output limit | 4096 |
| Chat/task budget | 600 seconds, 10 seconds cleanup reserve |
| Provider attempts | 20; no external CLI launches |
| Models actually requested | qwen3.8-flash: 17; qwen3.7-max: 3 auxiliary calls |
| Known billed tokens | 167,937 from 19 receipts |
| Complete total | Unknown: one cancelled request lacks final usage |
| Supervisor elapsed | 652.82 seconds, plus 9.28 seconds of zero-call startup preflight |
| Independent complete delivery | Failed |
| Task execution/download phases | Not started |
| Process cleanup | No remaining or forcibly killed processes; kernel close returned 200 |

The first diagnostic turn read the actual data but a negative plan instruction
was misclassified; a plan-contract failure message replaced the useful response
while metadata still said completed. The next turn created a real four-node tree,
then automatic refinement changed the cleaning rules and added two nodes. The
turn hit its own 600-second deadline. Its terminal run was correctly failed.
The new plan also lacked an owner, so the owner's plan list returned `[]`.

The raw records retain the original list response, SSE, call events, argument
payloads, database snapshots, input hash, model settings and unfinished phases.
An original database snapshot was taken before cleanup. Both running job copies
were then reconciled to the authoritative failed chat terminal, with no task
status promoted. The isolated restart probe reports zero active runs/jobs.
Missing usage remains unknown; no further paid calls were admitted.

## Fixes derived from the real failure

- Recognize Chinese/English negative creation instructions while preserving an
  affirmative request to create now and execute later.
- Propagate unsatisfied structured plan contracts into failed response metadata
  and control-job results; confirmation requests remain distinct.
- Forward the effective thinking setting through decomposition/material calls.
- Preserve `ToolContext.owner_id` when building plan generation context, so a
  plan created by a user is visible to that user.
- With the new output-contract policy enabled, explicit seeded tasks with
  authoritative acceptance are persisted without automatic decomposition,
  rubric rewriting or phase narration. The original request remains recorded.
  Manual plan editing/optimization remains available; historical unmarked plans
  retain their existing creation behavior.
- Reconcile historical chat-control job copies only when the same run, owner
  and session have an immutable terminal and no live lease. Keep history and
  preserve already-terminal job rows. Run after worker lease release and before
  startup stale-job recovery. The restart probe itself stays read-only.

## Deterministic evidence and limits

The new HTTP journey uses scripted model responses with real routes, plan
storage, Python cells, PlanExecutor, verification, manifest publication and file
downloads. It checks negative creation intent, owner-visible root/three-task
tree, explicit dependencies, output-contract markers, input-driven cleaning and
statistics, producer ownership, canonical file bytes and terminal SSE replay
without a new model call. Existing native/strict and interruption recovery
journeys remain in the regression suite.

The initial candidate passed 3,526 backend tests (after the Linux input-path
fix), 229 frontend tests and a complete production frontend build; CI run
`38269307803` passed Python 3.10/3.12, frontend, Compose and sandbox-image jobs.
Follow-up fixes have separate targeted and full-suite evidence; the release
must use CI for the final commit, not that earlier green run.

This is one diagnostic attempt, not a before/after comparison. No real-model
completion-rate improvement is claimed. The follow-up code has deterministic
coverage but has not been retried with a real model. Required output contracts
and background plan budgets remain off until that release gate is met.

## Rollback / remaining work

No production migration or service restart was performed. Baseline images and
production data remain unchanged. The branch can be reviewed independently.
Turning off `PLAN_OUTPUT_CONTRACTS_REQUIRED` stops marking new task definitions;
already-marked tasks still require declared acceptance. Turning off
`PLAN_EXECUTION_BUDGET_ENABLED` restores the previous plan budget policy.

Remaining: approved real-model verification of the follow-up candidate, a
production sandbox/UI smoke for that release, and the separately scoped long
project retention / joint database-manifest-file restore exercise. Whole-plan
budget coverage currently targets background jobs and PlanExecutor.execute_plan;
the legacy synchronous HTTP loop has per-task limits and is not claimed to have
the new enclosing background-job budget.
