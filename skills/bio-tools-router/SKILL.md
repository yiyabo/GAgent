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
4. Only fall back to `claude_code` after at least one valid bio_tools attempt fails.

## Input strategy
- Pass local absolute paths for `input_file` and path-like params.
- Keep output names relative where possible (e.g., `aln.sam`, `stats.txt`).

## Reference
- Verified operation list: `references/verified_ops.md`
