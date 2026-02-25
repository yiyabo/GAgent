---
name: "bio-tools-remote-reference"
description: "Runtime-verified bio_tools reference for local development + remote server execution."
updated_at: "2026-02-23 03:07:44 CST"
---

# Bio Tools Remote Reference

## Coverage Snapshot
- Generated at: `2026-02-23 03:07:44 CST`
- Tier-0 operation discoverability: `62/62` PASS
- Execution totals: PASS `43`, FAIL `2`, SKIPPED_PRECONDITION `17`
- API sampling: `12/12` successful `/tools/bio-tools` calls

## Tier Breakdown
| Tier | PASS | FAIL | SKIPPED_PRECONDITION |
|---|---:|---:|---:|
| Tier-1 | 29 | 0 | 6 |
| Tier-2 | 14 | 2 | 11 |

## Runtime-Verified PASS Operations
| Tool | Operation | Tier | last_run_id |
|---|---|---|---|
| `blast` | `makeblastdb` | Tier-1 | `33659112ac8a` |
| `bowtie2` | `build` | Tier-1 | `ba0ed530806c` |
| `bwa` | `index` | Tier-1 | `f637b90d9282` |
| `checkv` | `complete_genomes` | Tier-2 | `568b8d7557fc` |
| `checkv` | `completeness` | Tier-2 | `83e0ac2161ba` |
| `checkv` | `end_to_end` | Tier-2 | `e7d712024d50` |
| `fastani` | `compare` | Tier-1 | `da01e0da1745` |
| `flye` | `hifi` | Tier-2 | `e85638d68c29` |
| `flye` | `meta` | Tier-2 | `562159f2a28e` |
| `flye` | `ont` | Tier-2 | `4c660f43aa5b` |
| `hmmer` | `hmmbuild` | Tier-1 | `24699ff49f76` |
| `hmmer` | `hmmpress` | Tier-1 | `7958c3ca43bf` |
| `hmmer` | `hmmscan` | Tier-1 | `5fad1626722d` |
| `hmmer` | `hmmsearch` | Tier-1 | `964575313bc7` |
| `megahit` | `assemble` | Tier-2 | `1976dc23f4e3` |
| `metabat2` | `bin` | Tier-1 | `6d04481fe3a4` |
| `metabat2` | `depth` | Tier-1 | `47ceb50e8cf0` |
| `minimap2` | `filter` | Tier-1 | `8f77a23be48c` |
| `minimap2` | `map` | Tier-1 | `87fc70f7fea1` |
| `mmseqs2` | `easy_linclust` | Tier-2 | `7fe3826bfda3` |
| `mmseqs2` | `easy_search` | Tier-2 | `2882ba6b629f` |
| `nanoplot` | `basic` | Tier-1 | `842112fc0ac8` |
| `nextflow` | `run` | Tier-2 | `d7e33096e46b` |
| `ngmlr` | `map` | Tier-1 | `27087b63e0ee` |
| `prodigal` | `meta` | Tier-1 | `815cabb5d157` |
| `prodigal` | `predict` | Tier-1 | `09de39b25efc` |
| `samtools` | `index` | Tier-1 | `f23a40cf509a` |
| `samtools` | `sort` | Tier-1 | `ecfc17c19def` |
| `samtools` | `stats` | Tier-1 | `58126ab354a2` |
| `samtools` | `view` | Tier-1 | `4f20d97ed913` |
| `samtools` | `view_filter_unmapped` | Tier-1 | `c7315fdb74ed` |
| `seqkit` | `grep` | Tier-1 | `f3e4b0bc629e` |
| `seqkit` | `head` | Tier-1 | `5c24288ee5da` |
| `seqkit` | `seq` | Tier-1 | `d471d374655d` |
| `seqkit` | `stats` | Tier-1 | `92cff6c38a3f` |
| `seqtk` | `sample` | Tier-1 | `2d4811d07283` |
| `seqtk` | `size` | Tier-1 | `89152864f40c` |
| `snakemake` | `dry_run` | Tier-2 | `da0a21163e3a` |
| `snakemake` | `run` | Tier-2 | `17e7736b68f8` |
| `sniffles2` | `call` | Tier-1 | `424786ea99af` |
| `trim_galore` | `trim` | Tier-1 | `cdc530e01fc9` |
| `trim_galore` | `trim_paired` | Tier-2 | `bf526cc36e66` |
| `vibrant` | `run` | Tier-2 | `562b7b76af5b` |

## Failed Operations
| Tool | Operation | Tier | Reason (tail) |
|---|---|---|---|
| `iphop` | `predict` | Tier-2 | Command timed out after 1200 seconds |
| `virsorter2` | `run` | Tier-2 | e; note that snakemake uses bash strict mode!)  Exiting because a job execution failed. Look above for error message   *** An error occurred. Detailed errors may not be printed for |

## Skipped (Precondition)
| Tool | Operation | Tier | Reason |
|---|---|---|---|
| `bakta` | `annotate` | Tier-2 | remote bakta database version is incompatible with container image (requires v6.x) |
| `blast` | `blastn` | Tier-1 | requires BLAST DB side-files packaging across runs, which is not supported by current matrix runner |
| `blast` | `blastp` | Tier-1 | requires BLAST DB side-files packaging across runs, which is not supported by current matrix runner |
| `bowtie2` | `align` | Tier-2 | requires bowtie2 index prefix with multi-file side outputs from a prior run |
| `bwa` | `mem` | Tier-1 | requires reference side-index files alongside reference path |
| `checkm` | `lineage_wf` | Tier-2 | checkm container runtime bug (python exception in checkmData) on remote image |
| `checkm` | `qa` | Tier-2 | requires tree/output from prior checkm lineage workflow run |
| `concoct` | `bin` | Tier-2 | requires coverage/depth table for concoct bin |
| `das_tool` | `integrate` | Tier-2 | requires pre-generated multiple binning result sets and labels |
| `dorado` | `basecall` | Tier-1 | requires POD5/FAST5 raw signal dataset not included in smoke assets |
| `dorado` | `demux` | Tier-1 | requires Dorado basecall output/BAM layout not included in smoke assets |
| `genomad` | `annotate` | Tier-2 | genomad remote database content/schema is incompatible with current container |
| `genomad` | `end_to_end` | Tier-2 | genomad remote database content/schema is incompatible with current container |
| `genomad` | `find_proviruses` | Tier-2 | requires annotate-generated *_genes.tsv and *_proteins.faa in the same run directory |
| `gtdbtk` | `classify_wf` | Tier-2 | requires directory-based genome_dir upload workflow not covered by current minimal smoke assets |
| `maxbin2` | `bin` | Tier-2 | requires abundance profile for maxbin2 bin |
| `nextflow` | `clean` | Tier-1 | requires same working directory and execution history from a prior nextflow run, but matrix runner uses isolated run dirs per operation |

## Agent Usage Guidance
1. Prefer operations marked PASS for production tasks.
2. For FAIL entries, check `bio_tools_test_matrix.json` and inspect `stderr_tail` + artifact directories.
3. For SKIPPED_PRECONDITION entries, prepare required data/dependencies first, then rerun matrix.

## Artifact Paths
- Machine-readable matrix: `/Users/apple/LLM/agent/bio_tools_test_matrix.json`
- Skill directories: `/Users/apple/LLM/agent/skills/bio-tools-router`, `/Users/apple/LLM/agent/skills/bio-tools-execution-playbook`, `/Users/apple/LLM/agent/skills/bio-tools-troubleshooting`
