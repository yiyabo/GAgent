# Agent-led Plan execution; no independent acceptance phase

The user replaced the earlier acceptance-first policy: a Plan task records that
it ran; file quality and missing inputs belong to the existing task Agent's work,
not an independent verifier or a human approval queue. This supersedes the
acceptance requirements in the previous optimization plans.

`PLAN_ACCEPTANCE_ENABLED=0` is the application/deployment default. A single
completion policy governs finalization, historical effective states, dependency
readiness, native/strict loops, external code delegation and UI result actions.
The former pipeline remains behind `=1` for rollback and its regression coverage.

- A normally finished task is `completed` and shown as 已执行. This is execution
  lifecycle, not a scientific-quality assertion. Genuine runtime failures,
  cancellation, deadlines and uncertain mutations retain their runtime states.
- Acceptance checks, schema quality rules, required publications and alias
  preflight do not reject a finished task or launch contract-repair rounds.
  Existing criteria and raw model reports remain historical data; no threshold
  is silently edited and no acceptance result is invented.
- Input aliases are discovery hints. The current Agent may inspect real files,
  choose an equivalent input, recover materials or write a necessary supplement.
  Resolving every alias is optional. Bindings still record real content hashes
  and provenance when used; tools cannot publish nonexistent files.
- Explicit dependency ordering, actual execution budgets, cancellation and
  publication ownership/version fences remain. Runtime artifact inference must
  not add dependency edges or restructure the persisted task tree. Only the user
  authorizes tree edits; internal todo lists and supporting input work are allowed.
- Native and strict Plan loops omit the separate acceptance guard, including its
  missing-output extensions. Ordinary chat output handling and standalone code
  contracts remain compatible. External Plan delegation receives the same input
  recovery guidance instead of deterministic acceptance instructions.
- The result UI hides re-verification and manual approval actions in Agent mode.
  Legacy acceptance endpoints return 410 after authorization. Result APIs retain
  raw history and add execution_policy=agent for presentation. Historical tasks
  rejected solely by acceptance are projected using their recorded execution
  state; running/pending tasks cannot be promoted from an older successful payload.

Tests explicitly exercise the new default with scripted providers and real
native/strict controllers/PlanExecutor, including downstream supplementation
from retained material, unchanged task definitions, outputless runs, genuine
runtime failure and ordinary-chat compatibility. Legacy suites select `=1` as
rollback coverage. No new paid model campaign or scientific job is authorized.

Release requires full backend/frontend CI, a complete frontend build, isolated
image tests and read-only replay of the real #187 records/files. Deploy normally
after the idle gate and database/manifest/config backups. Set the production flag
explicitly to zero. #16 should project as 已执行, keep its existing files/rules,
and no longer block #17 on an acceptance result. Publishing and subsequent task
execution remain existing mechanisms and are not launched by this rollout.
