---
name: bio-tools-execution-playbook
description: "Execution playbook for bio_tools, with request examples and I/O conventions. Trigger when agent needs concrete payloads for tool_name/operation/params."
---

# Bio Tools Execution Playbook

## Workflow
1. Call `help` for the chosen tool and derive the minimal required params. The configured catalog is not proof that an image, runtime, or reference database is ready.
2. Use `references/request_templates.json` only as payload examples. Its `/private/tmp` and `/Volumes/BIOINFO2` paths are old test fixtures; replace every input path with a file that exists in this run.
3. Execute with bio_tools in the configured mode and inspect `success`, `execution_mode`, `output_path` or `produced_files`, and any error. Remote runs may also return `run_id` and `local_artifact_dir`.
4. Chain dependent operations only after checking the actual output files.

## I/O conventions
- Inputs: absolute local paths preferred.
- Outputs: short relative names.
- Reuse verified `output_path` or `produced_files` for dependent calls; remote runs may use `local_artifact_dir`.

## Canonical chains
- `makeblastdb -> blastn/blastp`
- `bwa index -> bwa mem`
- `samtools view -> sort -> stats`

## Reference
- Templates: `references/request_templates.json`
