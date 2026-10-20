---
name: bio-tools-router
description: "Route bioinformatics intents to verified bio_tools operations, prioritizing PASS cases from bio_tools_test_matrix.json. Trigger on FASTA/FASTQ/SAM/BAM requests, assembly, alignment, annotation, taxonomy, phage, or workflow execution tasks."
---

# Bio Tools Router

## When to use
- Task includes FASTA/FASTQ/SAM/BAM/contigs input.
- User asks for sequence stats, alignment, gene prediction, phage checks, or workflow runner calls.

## Routing rules
1. Treat `references/verified_ops.md` as historical test evidence, then check the current tool's `help` and runtime result.
2. If operation choice is uncertain, call `bio_tools(operation="help")` for candidate tools first.
3. Favor a historically verified operation only when the current backend, inputs, and database support it.
4. If the tool is not configured or its runtime/database is unavailable, explain the limitation or use a suitable available alternative. Do not repeatedly retry an unavailable tool.

## Input strategy
- Pass local absolute paths for `input_file` and path-like params.
- Keep output names relative where possible (e.g., `aln.sam`, `stats.txt`).

## Reference
- Verified operation list: `references/verified_ops.md`
