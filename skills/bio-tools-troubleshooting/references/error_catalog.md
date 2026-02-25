# Error Catalog

Grouped from FAIL and SKIPPED_PRECONDITION records in bio_tools_test_matrix.json.

## Command timed out after 1200 seconds
- `iphop` / `predict` (FAIL, Tier-2)

## checkm container runtime bug (python exception in checkmData) on remote image
- `checkm` / `lineage_wf` (SKIPPED_PRECONDITION, Tier-2)

## e; note that snakemake uses bash strict mode!)
- `virsorter2` / `run` (FAIL, Tier-2)

## genomad remote database content/schema is incompatible with current container
- `genomad` / `annotate` (SKIPPED_PRECONDITION, Tier-2)
- `genomad` / `end_to_end` (SKIPPED_PRECONDITION, Tier-2)

## remote bakta database version is incompatible with container image (requires v6.x)
- `bakta` / `annotate` (SKIPPED_PRECONDITION, Tier-2)

## requires BLAST DB side-files packaging across runs, which is not supported by current matrix runner
- `blast` / `blastn` (SKIPPED_PRECONDITION, Tier-1)
- `blast` / `blastp` (SKIPPED_PRECONDITION, Tier-1)

## requires Dorado basecall output/BAM layout not included in smoke assets
- `dorado` / `demux` (SKIPPED_PRECONDITION, Tier-1)

## requires POD5/FAST5 raw signal dataset not included in smoke assets
- `dorado` / `basecall` (SKIPPED_PRECONDITION, Tier-1)

## requires abundance profile for maxbin2 bin
- `maxbin2` / `bin` (SKIPPED_PRECONDITION, Tier-2)

## requires annotate-generated *_genes.tsv and *_proteins.faa in the same run directory
- `genomad` / `find_proviruses` (SKIPPED_PRECONDITION, Tier-2)

## requires bowtie2 index prefix with multi-file side outputs from a prior run
- `bowtie2` / `align` (SKIPPED_PRECONDITION, Tier-2)

## requires coverage/depth table for concoct bin
- `concoct` / `bin` (SKIPPED_PRECONDITION, Tier-2)

## requires directory-based genome_dir upload workflow not covered by current minimal smoke assets
- `gtdbtk` / `classify_wf` (SKIPPED_PRECONDITION, Tier-2)

## requires pre-generated multiple binning result sets and labels
- `das_tool` / `integrate` (SKIPPED_PRECONDITION, Tier-2)

## requires reference side-index files alongside reference path
- `bwa` / `mem` (SKIPPED_PRECONDITION, Tier-1)

## requires same working directory and execution history from a prior nextflow run, but matrix runner uses isolated run dirs per operation
- `nextflow` / `clean` (SKIPPED_PRECONDITION, Tier-1)

## requires tree/output from prior checkm lineage workflow run
- `checkm` / `qa` (SKIPPED_PRECONDITION, Tier-2)
