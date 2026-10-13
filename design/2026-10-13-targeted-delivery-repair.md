# Delivery repair: reuse the existing loop

Baseline: c4f9f80a. Feed missing outputs and concrete errors back to the existing Agent, then use the existing verifier to decide completion.

The existing PlanExecutor contract-repair method remains the sole automatic output repair path. After verification returns a contract mismatch, it sends the missing items, existing file references and recent errors back to the same agent. The prompt asks it to inspect real data fields, preserve valid outputs and fill only the gap. A second attempt asks for a simpler method. There is no new repair controller, snapshot subsystem, tool isolation layer or rollout flag.

Default repair attempts change from one to two; the existing PLAN_EXECUTOR_CONTRACT_REPAIR_ATTEMPTS setting and per-run zero/1–3 overrides remain compatible. Repair temporarily lowers the existing controller's base iteration limit to 12 and retains its normal closeout behavior. Cancellation and any existing parent deadline remain authoritative; this change adds no independent wall-clock budget. The original task context's mandatory execution-tool instructions are replaced by a focused repair instruction while its output specification and evidence references remain available.

All backends can feed a completed-but-incomplete result into this method. A cancelled or failed external backend is not reinterpreted as completed. The route layer no longer restarts the whole task three times or launches a second delegated audit repair. Explicit standalone audit APIs remain unchanged. Plan-level optional auto-recovery does not rerun a contract mismatch after this method returns.

Verification stays unchanged. Repair copies forward the original result metadata and files, incorporates the repair controller's new produced-file evidence, and reruns the existing verifier and publisher. The preservation rule is a prompt instruction; it does not introduce filesystem rollback guarantees. Existing job events and the job panel show the repair attempt count; raw logs retain earlier phases.

Validation uses fixed model responses, real native/strict controllers, real Python kernels, verification/publication, route call-count checks and frontend replay tests. No additional paid trial was authorized by the user's simplification reply, so no real model trial or user-task rerun is launched automatically. Model success-rate improvement is not established by deterministic tests.

Rollback: revert the code through the normal idle/backup/image gates; the existing repair count can be set to zero to disable automatic repair. No data migration or new database state is required.
