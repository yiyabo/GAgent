# TOOL BOX

## OVERVIEW
Tool Box is the executable tool ecosystem used by DeepThink and plan tasks: registry, routing metadata, cache, MCP server, bio tools, and concrete handlers.

## STRUCTURE
```
tool_box/
├── tool_registry.py     # Declarative standard/custom tool definitions
├── tools.py             # Runtime registry and ToolDefinition metadata
├── router.py            # Tool selection logic
├── context.py           # ToolContext propagated from backend executor
├── tools_impl/          # Concrete tool handlers; many large stateful modules
└── bio_tools/           # Domain-specific bioinformatics tool wrapper/resources
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Add tool | `tool_registry.py`, `tools_impl/` | Define handler module and add to `_STANDARD_TOOLS` or `_CUSTOM_TOOLS`. |
| Metadata | `_TOOL_METADATA` in `tool_registry.py` | Read-only/concurrent/destructive/search hints drive orchestration. |
| Runtime registry | `tools.py` | `ToolDefinition`, categories, search. |
| Code execution | `tools_impl/code_executor.py` | Docker-backed execution and guardrails. |
| Code mode | `tools_impl/execute_code/` | Env-gated (`CODE_MODE_ENABLED=1`) programmatic tool calling: persistent Python kernel + loopback RPC into the registry. |
| Skill loading | `tools_impl/load_skill.py` | On-demand full SKILL.md retrieval over `app/services/skills` (progressive disclosure; read-only, always on). |
| PhageScope | `tools_impl/phagescope.py` | API payload quirks and tracking. |
| Bio tools | `bio_tools/` | Remote bioinformatics execution wrapper. |
| Deliverables | `tools_impl/deliverable_submit.py` | Explicit artifact publication surface. |
| Bio config | `bio_tools/tools_config.json` | Operation commands, images, parameters, schema source of truth. |

## CONVENTIONS
- Tool handlers are async and accept `tool_context` when they need session/plan/task/work_dir identity.
- New tool definitions must include schema, category, handler, tags/examples if useful, and orchestration metadata when non-default behavior matters.
- Conservative default: mutating and not concurrent-safe unless explicitly marked.
- `result_interpreter` is not globally read-only because some operations execute generated code.
- Bio tools expose one registry entry that dispatches configured operations from `bio_tools/tools_config.json`.
- Bio tools validate paths/control characters/types, support FASTA text or file inputs, and can run local/remote/background jobs.

## TOOL RETIREMENT CHECKLIST
Removing a tool is a multi-surface operation; a registry-only removal leaves
live residue (the 2026-09 `deeppl` half-removal is the cautionary tale). Work
through every item and verify with `grep -rn "<tool_name>" app tool_box web-ui/src`:
1. `tool_registry.py`: remove the definition and its `_TOOL_METADATA` entry.
2. `tool_box/tools_impl/<tool>.py`: delete the handler module.
3. `app/services/tool_schemas.py`: remove the hand-written native-call schema.
4. Prompt surfaces: tool catalogs/descriptions in `app/services/deep_think/prompts.py`,
   delegation allowlists (plan executor / code_executor prompts), skills references.
5. Result/summary branches: `app/routers/chat/tool_results.py` and any
   tool-specific post-processing in `app/routers/chat/action_execution.py`
   (signals, consensus, galleries) — including consumers of that metadata.
6. Frontend: tool-name literals in `web-ui/src` (labels, icons, render branches).
7. Tests: delete/rework tool-specific tests; keep absence-guard assertions
   (e.g. "tool_operation: <tool> not in base actions") where they exist.
8. Scripts: `grep -rn "tools_impl.<tool>" scripts/` — offline pipelines that
   import the handler must be migrated, deleted, or explicitly flagged to the
   user before removal.
9. Run `pytest app/tests/chat app/tests/unit app/tests/tools -q` and compare
   against the pre-change failure baseline.

## TESTS
```bash
pytest app/tests/tools/test_bio_tools_schema_and_skills.py -v
pytest app/tests/tools/test_execution_semantics_regressions.py -v
```

## ANTI-PATTERNS
- Do not mark a mutating tool as read-only to satisfy probe-loop logic.
- Do not bypass PhageScope payload builder; required format is unusual.
- Do not add shell/SSH destructive behavior without preserving approval and timeout semantics.
- Do not edit bio tool schemas without keeping `tools_config.json`, registry schema, and skills in sync.
