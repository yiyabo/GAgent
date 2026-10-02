# Shared execution semantics and workflow evaluation

This phase joins the runtime rules beneath chat, native plan tasks and external
code-agent adapters. It preserves the research project/DAG layer and the existing
native/strict model loops. It is not a replacement of every entry point or backend.

## Runtime changes

- One context factory carries owner, provider, attachments, recall, learned skills,
  original request and live artifact-registry references into explicit task,
  cascade, full-plan and rerun entry points. Explicit execution no longer disables
  Skills independently of other plan execution.
- Native chat/task controller calls share the same bounded execution-stage wrapper.
- One verdict policy handles backend failure/cancellation, authoritative output
  rejection and uncertain steps. A missing metadata status cannot make a rejected
  output report a successful chat run. Failed backends cannot be rescued by files.
- Explicit execution checks effective plan acceptance before skipping old completed
  nodes; missing outputs trigger execution/repair rather than a completed summary.
- External delegation has an atomic task receipt and checkpoint. Its completion is
  recorded after the existing verification/materialization pipeline. This records
  the external operation, not fictional CLI-internal tools. Resume reuses verified
  receipts and rechecks/materializes plan state; unknown mutations, changed
  contracts/workspaces and changed/missing outputs require reconciliation.
- Native/delegate checkpoints cannot silently cross execution backends. Existing
  old delegated attempts without a checkpoint remain unsupported for continuation.
- Verified output delivery cannot finish with only a retry/process message. The
  deterministic fallback lists checked files and states the output-check boundary;
  it never adds a scientific conclusion or upgrades rejected/uncertain results.

## Evaluation corpus

`research-workflows-v1` freezes small inputs for table cleaning, figure data,
FASTA filtering, local-source literature reports, user corrections and procedural
reuse. Independent code checks actual records and values, image readability and
nonblankness, citation inventory and replacement of stale results. Scientific
validity and report/figure quality remain separate human-review dimensions.

`run_harness_workflow_eval.py` uses a fresh isolated DB/runtime/workspace and the
actual production native controller, LLM client and execute_code kernel. Modes
chat-native and plan-native compare controller entry profiles; they are not HTTP
browser or external-CLI end-to-end evaluations. Default live pilot is three cases,
at most five top-level provider calls per case, 1200 output tokens per call and a
bounded case deadline. Tokens and elapsed time are recorded; dollar costs remain
unknown without gateway/cache pricing. No business sessions are used or backfilled.

The baseline pilot ran against deployed 1113d9a0. Startup adapter errors are not
valid baseline observations. Its valid small pilot had 2/3 file-content deliveries
and 1/3 complete answer deliveries; FASTA files were correct but the final answer
was retry prose. The same raw outputs were regraded under a fixed delivery-plus-
answer rule before candidate evaluation. This is a small diagnostic pilot, not a
production success-rate estimate or proof of increased throughput.

## Verification

Core regressions cover owned replay without duplicate delegate execution,
uncertain mutation refusal, contract/workspace changes, modified outputs,
cancellation with preexisting files, effective-status skipping, inherited context,
common terminal truth and checked-delivery fallback. Corpus oracles deliberately
reject incorrect statistics, stale means and invented citations.
