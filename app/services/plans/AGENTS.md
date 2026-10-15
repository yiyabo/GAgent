# PLAN SERVICES

## OVERVIEW
Plan services own DAG decomposition/execution, dependency normalization, artifact contracts, status resolution, and verification authority.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Execute DAG | `plan_executor.py` | Main hotspot; enforces dependencies, runs tools, materializes artifacts. |
| Decompose tasks | `plan_decomposer.py` | Creates task trees from goals. |
| Data models | `plan_models.py` | `PlanNode`, `PlanTree`, adjacency helpers. |
| Dependency rules | `dependency_validation.py`, `dependency_enrichment.py` | Self/missing/ancestor/descendant/cycle handling; composite expansion. |
| Artifact authority | `artifact_contracts.py` | Alias specs, canonical paths, manifest load/save/resolve. |
| Verification | `task_verification.py` | Acceptance criteria, artifact authority demotion, manual acceptance. |
| Effective status | `status_resolver.py` | Raw status + payload + manifest + dependencies -> effective state. |
| Background jobs | `decomposition_jobs.py` | Job lifecycle and logging. |

## CONVENTIONS
- Default `PLAN_ACCEPTANCE_ENABLED=0`: completion records execution, not independent output approval. Missing output/alias declarations are guidance for the existing Agent, which may inspect, reuse and supplement upstream materials within its assigned task.
- Canonical manifests remain the version/provenance store for actual published files. Runtime ownership and immutable publication fences still apply; missing publications do not demote finished executions in the default mode.
- `PLAN_ACCEPTANCE_ENABLED=1` restores the legacy strict output/alias acceptance and repair pipeline; its tests remain as rollback coverage.
- Dependency normalization is generic and structural; avoid plan-specific fixes.
- Composite dependencies should expand to executable leaf tasks; child-to-parent dependencies are invalid.
- Contract repair should use `failure_kind=contract_mismatch` and `metadata.contract_diff`, not filename-specific hacks.

## TESTS
```bash
pytest app/tests/plan/test_plan_executor_deps.py -v
pytest app/tests/plan/test_status_resolver.py -v
pytest app/tests/tools/test_task_verification.py -v
```

## ANTI-PATTERNS
- Keep actual runtime failures, cancellations and uncertain mutations distinct from finished executions. Never describe execution completion as proof of scientific or deliverable quality.
- Do not guess among multiple producers for the same basename or alias.
- Do not hard-code task IDs, plan IDs, biological topics, or one-off artifact filenames.
- Do not add compatibility shims unless persisted external artifacts require them.
