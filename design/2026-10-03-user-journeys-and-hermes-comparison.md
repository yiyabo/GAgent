# User journeys and a bounded Hermes comparison

Status: implementation started on 2026-10-03, following the user's approval.
Starting GAgent revision: `0b890a0d63d5a8f6af82f98226f64e83f9fc20a7`.
The local `main` reference is stale; use the inspected working revision.

## Objective

Establish whether users receive correct, complete results through the real
execution paths, identify the causes of excessive repeated work, and compare
GAgent with a pinned Hermes revision under explicit comparable conditions.
Convert confirmed differences into small fixes, then validate each independently.
Test counts, implemented capabilities and measured user outcomes remain distinct.

The previous 108-trial and 48-trial studies remain incomplete historical records.
This campaign is new and versioned; it does not replace failed or unstarted rows
in those studies and does not automatically satisfy their release gates.

## Stage 1: trustworthy diagnostics and offline journeys

1. Audit high-cost original trials by unique provider attempt, input/output usage,
   context growth, tool failures, repeated calls and final delivery outcome.
2. Freeze the effective configuration for every entry, including disabled feature
   flags. Ambient process environment must not silently enable an experimental arm.
3. Persist start, attempt, usage and termination records incrementally. Recover
   interrupted trials without re-execution, retain known cost and represent
   missing usage explicitly. No trial silently disappears because a worker died.
4. Enforce per-trial and campaign limits before subsequent calls/trials where
   observable. Token enforcement is post-response; do not call it a provider quota.
   CLI internal calls may be unobservable: retain that limitation and enforce
   launch, turn and wall-clock bounds rather than inventing attempt counts.
5. Run production-path tests with mocked models for malformed parameters, output
   promotion, completion, cancellation, stale inputs, duplicate publication and
   isolation. Extend HTTP/SSE or browser tests where they add actual entry coverage.

Deliverables: cost/failure audit, reproducible configuration, supervisor tests and
an offline preflight result. No model traffic until isolation, terminal cleanup,
budget accounting and delivered-file checks pass.

## Stage 2: final GAgent source through the three entries

Run two local-file cases (cleaning and chart generation) through chat-native,
plan-native and plan-external: six trials on the same frozen source. These are
diagnostics, not a statistical completion-rate estimate. Preserve every failure.
Inspect complete artifacts, answer links, system verdict, oracle verdict,
effective model/provider and remaining processes. An infrastructure failure stops
the campaign for analysis; it is not fixed and retried under the same trial ID.

## Stage 3: same-task Hermes comparison

Pin a real upstream commit and executable/runtime versions. Use an isolated
Hermes home, workspace, sessions and output directory, with no personal memory,
private oracle, previous answers or unrelated installed skills. Do not modify the
user's Hermes configuration or reuse their conversation database.

Start with cleaning, chart and local-evidence report tasks, each on GAgent and
Hermes (six trials). Fix prompts, fixtures, output contract, model/provider,
output allowance and common external deadline; randomize order with a fixed seed.
Document tool and native API differences. If model or provider cannot be matched,
label the result a product/configuration comparison, not a harness-only effect.
Use common supervisor-owned content/delivery checks. Native task status alone is
not an independent success criterion. Scientific or visual correctness may still
require human review.

The remaining six trial slots are reserved for versioned multi-turn journeys
(editing a prior result, continuing across sessions, skill reuse) after their
offline state/ownership checks pass. They do not authorize extra retries or a
replacement sample after failure. Skills exposure, body delivery, file access and
verified execution are different observations.

## Finite campaign envelope

All paid stages share one durable campaign ledger; subprocesses and restarts do
not obtain a fresh allowance. The user has authorized starting this work; these
are the conservative limits selected for the first diagnostic campaign.

| Limit | Value |
|---|---:|
| Started trial processes | 18 total, including interrupted/failed starts |
| Active campaign wall time | 120 minutes total |
| Individual trial wall time | 600 seconds plus a separate 10-second cleanup allowance |
| Known aggregate tokens | 1,000,000 post-response stop threshold |
| Known tokens per trial | 150,000 post-response stop threshold |
| Observable provider attempts | 200 campaign / 12 per trial |
| External Agent CLI launches | 12 total; Hermes SDK workers count as trials |
| CLI turns | 12 per external trial |
| Normal output allowance | 4096 tokens |

Check known usage before a new model request where the adapter exposes it and
before every new trial. A single response can cross a token threshold; unknown
CLI usage is explicitly unknown. Stop after missing usage prevents responsible
accounting rather than treating it as zero. Native and CLI limits are reported
as different measurable controls, not equivalent compute budgets. This is a
bounded diagnostic profile, different from production 3600 seconds / 48 native
iterations / 120 CLI turns. Stop at the first exhausted campaign limit and list
unstarted trials. Do not expand limits automatically.

## Stage 4: targeted improvements and staged enablement

Based on observed failures, align tool availability, dispatch error semantics,
artifact publication and completion across entries. Reuse shared services and
preserve facade/monkeypatch contracts; avoid an unmeasured loop rewrite.

Validate each experimental behavior separately: parameter correction, disclosure,
soft finalization, artifact v2, skill loading and recommendations. If existing
flags bundle multiple policies, split diagnostic controls before attributing an
effect to one policy. Keep production defaults off until the applicable existing
release gate is satisfied; these diagnostic samples cannot silently weaken it.

The visible learning journey should demonstrate: verified task -> candidate
method -> appropriate new-task retrieval -> delivered method version -> new-input
execution -> independent check. Expose sources and unchecked dimensions, and
retain existing feedback pause/revalidation rules.

## Reports and release

Report per revision and entry: content pass, complete delivery, false completion,
user corrections, duration, provider/estimated/missing usage, cost per successful
task, tool/parameter failures, cancellation/resumption and pending manual checks.
Expose incomplete comparisons and tool differences; do not pool changed revisions.

Commit independently reviewable changes with focused tests, then required backend,
frontend and SSE regressions. Any production release follows HANDOFF: green CI,
no active tasks, backup, bundle, public/DB/runtime smoke, three-machine docs sync.
Keep immutable version history and compatible readers across rollbacks.

Primary references checked on 2026-10-03:
- https://hermes-agent.nousresearch.com/docs/developer-guide/architecture
- https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/
- https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/

These docs establish available mechanisms, not measured superiority over GAgent.
