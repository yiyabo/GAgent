# CHAT ROUTERS

## OVERVIEW
Chat routing converts user turns into `chat` or `execute_task`, builds task/session context, and hands off to DeepThink/tool execution.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| HTTP/SSE endpoints | `routes.py` | Chat request lifecycle and streaming surface. |
| Tier/intent routing | `request_routing.py` | Binary intent, request tier, plan lifecycle intent, explicit task override. |
| Agent handoff | `agent.py` + `continuation_hints.py`/`phagescope_rewrite.py`/`review_loop.py`/`response_metadata.py`/`task_context.py`/`deterministic_execute.py`/`code_executor_bridge.py`/`image_display.py`/`subject_grounding.py`/`unified_stream.py` | `agent.py` (≤4000 lines) is the facade: it keeps the class, the module-level bindings tests patch (`execute_tool`, `plan_decomposition_jobs`, job trio, `_persist_runtime_context`, `_save_chat_message`, `_set_session_plan_id`, stdlib aliases), and both compat bridges verbatim. Siblings read patched names at call time via `_ag()` (`from . import agent as facade`). `unified_stream.py` holds the extracted phases of `process_unified_stream`; the SSE event order is locked by `app/tests/chat/test_unified_stream_event_golden.py` — run it before touching that method. |
| Tool/action bridge | `action_handlers.py` | Structured actions and tool calls. |
| Guardrails | `guardrails.py`, `guardrail_handlers.py` | Explicit task scope, file/bio/code execution gates. |
| Subject tracking | `subject_identity.py` | Follow-up subject inheritance and path identity. |
| Tool summaries | `tool_results.py` | Evidence envelopes and result compaction. |

## CONVENTIONS
- `intent_type` is only `chat` or `execute_task`; do not add more intent buckets.
- `request_tier` controls depth and max iterations: `light`, `standard`, `research`, `execute`.
- Explicit numeric task mentions set `explicit_task_ids` / `explicit_task_override` and suppress plan optimize/review heuristics for that turn.
- Execute-task flows may include `plan_operation`, but true execution claims must be grounded in real tool results.
- Use logs to verify routing: `intent_type`, `request_tier`, `route_reason_codes`, `tools_used`.

## TESTS
```bash
pytest app/tests/chat/test_request_tier_routing.py -v
pytest app/tests/chat/test_deep_think_strict_protocol.py -v
pytest app/tests/chat/test_no_fallback_policy.py -v
```

## ANTI-PATTERNS
- Do not force execute mode for simple status questions about a bound task.
- Do not let probe-only cycles finish with success-shaped reports.
- Do not inherit stale file/directory subjects into unrelated questions.
- Do not replace failed tool execution with generic fallback prose.
- Do not change `process_unified_stream` or its extracted phases without running `app/tests/chat/test_unified_stream_event_golden.py` — the SSE event type/order/payload is a locked contract.
- Do not move a name out of `agent.py` before checking `grep -rn "<name>" app/tests/ | grep -i "patch\|setattr"`; patched names stay bound in the facade and siblings must reach them through `_ag()`.
