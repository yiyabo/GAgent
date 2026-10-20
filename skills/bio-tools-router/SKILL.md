---
name: bio-tools-router
description: "Route bioinformatics intents to verified bio_tools operations, prioritizing PASS cases from bio_tools_test_matrix.json. Trigger on FASTA/FASTQ/SAM/BAM requests, assembly, alignment, annotation, taxonomy, phage, or workflow execution tasks."
---

# Bio Tools Router

## When to use
- Task includes FASTA/FASTQ/SAM/BAM/contigs input.
- User asks for sequence stats, alignment, gene prediction, phage checks, or workflow runner calls.

## Routing rules
1. Prefer operations listed in `references/verified_ops.md`.
2. If operation choice is uncertain, call `bio_tools(operation="help")` for candidate tools first.
3. Prefer Tier-1 PASS operations before Tier-2 operations when both satisfy intent.
4. Only fall back to `code_executor` after at least one valid bio_tools attempt fails.

## Native servers (host software through Slurm)
On a server running `BIO_TOOLS_EXECUTION_MODE=native`, `bio_tools(operation="list")` returns only what can run now.
- Trust `list` and `help` over `references/verified_ops.md` (that table comes from the Docker test matrix).
- `unavailable_tools` and `unavailable_operations` carry a reason (`database_unavailable`, `environment_not_installed`,
  `program_not_installed`). Tell the user what is missing; do not retry or script around it.
- Phage work: `genomad end_to_end` (virus/plasmid calls and taxonomy), `checkv end_to_end` (quality),
  `virsorter2 run`, `vibrant run`, `bakta annotate`, `prodigal` for genes; `checkm` is for bacterial bins, so a
  phage genome scores 0% there by design.

## Input strategy
- Pass local absolute paths for `input_file` and path-like params.
- Keep output names relative where possible (e.g., `aln.sam`, `stats.txt`).

## Reference
- Verified operation list: `references/verified_ops.md`
