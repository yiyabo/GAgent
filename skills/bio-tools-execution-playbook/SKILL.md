---
name: bio-tools-execution-playbook
description: "Execution playbook for bio_tools remote mode, with reusable request templates and I/O conventions. Trigger when agent needs concrete payloads for tool_name/operation/params."
---

# Bio Tools Execution Playbook

## Workflow
1. Start from `references/request_templates.json`.
2. If template missing, call `help` and derive minimal params.
3. Execute with bio_tools in remote mode and inspect metadata (`run_id`, `remote_run_dir`, `local_artifact_dir`).
4. Chain dependent operations only after verifying required artifacts exist locally.

## I/O conventions
- Inputs: absolute local paths preferred.
- Outputs: short relative names.
- Reuse `local_artifact_dir` outputs for dependent calls when possible.

## Canonical chains
- `makeblastdb -> blastn/blastp`
- `bwa index -> bwa mem`
- `samtools view -> sort -> stats`

## Reference
- Templates: `references/request_templates.json`
