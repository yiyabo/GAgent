# Independent runtime controls and follow-up journeys

Starting revision: 4c481137. Continue the existing bounded journey campaign; do
not refresh its 18-trial, 120-minute, 1M post-response-token allowances.

## Default-on correctness changes

- Recalled assistant links are resolved against their source session, after the
  existing owner/project filter. Up to four local references carry source_path,
  existence and `version_status=untracked`; original messages are unchanged.
  These are current locations, not verified historical snapshots. No missing
  files or sessions are created. The recall envelope remains bounded at 6500
  characters. Unsupported/external links remain only in the original message.
- Plan callers omitting the optional skill run ID infer it only from a matching
  active chat claim and session owner. Index exposure and actual body loading
  remain distinct; no success or promotion is inferred from exposure.
- A non-streaming repair replaces the response metadata as well as tool calls.
  Its completed HTTP response is not misclassified as the earlier incomplete
  stream. Usage for both physical attempts remains separately counted.

## Independent experimental controls

| Setting | Default | Scope |
|---|---|---|
| AGENT_ARGUMENT_VALIDATION_ENABLED | unset | Inherit AGENT_RUNTIME_V2_ENABLED; explicit 0/1 overrides native argument validation and bounded truncation correction |
| AGENT_SCHEMA_DISCLOSURE_V2_ENABLED | unset | Inherit the umbrella; explicit 0/1 selects core-first monotonic disclosure |
| AGENT_TOOL_RECEIPT_COMPACTION_ENABLED | 0 | JSON minification and removal of duplicate dispatch-envelope status fields, model messages only |

The broader request-budget policy still follows the umbrella. A raised repair
output allowance is reserved even when argument validation is enabled alone.
Checkpoint state freezes these policies, disclosure/force-full state and repair
allowance; legacy checkpoints keep their known legacy strategy. The schema-only
policy uses local gagent_tools.list_tools/describe rather than resending complete
Python signatures; full schemas remain available through explicit loading or
force-full recovery. Previously disclosed plan tools remain disclosed after an
unbind. Existing facade and monkeypatch surfaces are retained.

Receipt projection does not summarize stdout, file contents, provenance, errors
or truncation markers. Raw callback/step/verification data is unchanged. On 33
unique prior tool messages, it reduced characters 62268 -> 60102 (3.48%). That is
an offline text-size measurement, not billed-token or completion-rate evidence.
All new experimental behavior stays off in production pending its release gate.

## Verification layers

- Scripted-provider HTTP/SSE tests use real upload, controller, kernel, publishing,
  SQLite, history and file download. Native/strict and umbrella OFF/ON cover
  corrected statistics with a preserved old file, new project-session recall,
  and natural skill-index -> body -> verified output delivery. Unrelated project
  history is excluded. These tests verify plumbing, not LLM quality.
- The optional correction_journey corpus case performs two real controller/SDK
  turns: group means, then group medians while copying the original output bytes.
  The supervisor checks both contents, the first-output hash and unchanged input.
  The original default six-case suite stays at 18 trials. Corpus v2/oracle v5 add
  the journey without overwriting old reports.
- The paid controller/SDK journey is not an HTTP/browser benchmark. GAgent passes
  normal user/assistant history; Hermes receives its returned conversation history.
  Thinking controls and history handling differ, so no harness-only speed claim.
- A natural lexical skill probe uses curated fixture procedures, no embedding
  requests. Its first report failed after execution because the evaluator read an
  obsolete variable. Original failure and cost remain; a separate read-only
  reconstruction verifies persisted task/output/body-delivery facts. The logger
  now has a single-turn serialization regression. Do not rerun the failed trial.

At the end of this batch, the shared campaign has 12 started trials and 919114
known tokens, including 10743 estimated CLI tokens. No automatic extra runs.
Cross-session real-model and balanced Skills comparisons, browser journeys and
the older full 108/48-trial efficacy gates remain unfinished.

Release: focused tests, full backend/CI, Python 3.10 checks, then HANDOFF's guarded
bundle deployment and three-machine document checksums. Preserve config and
immutable artifact history; keep the independent experiment flags disabled.
