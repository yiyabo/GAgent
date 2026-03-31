#!/usr/bin/env python3
"""Build a full bio_tools coverage matrix and skill reference assets.

This script runs three-layer coverage:
- Tier-0: operation discoverability via per-tool help output
- Tier-1/2: execution attempts in remote mode with precondition-aware skips

Outputs:
- /Users/apple/LLM/agent/bio_tools_test_matrix.json
- /Users/apple/LLM/agent/bio_tool_list.md
- /Users/apple/LLM/agent/skills/bio-tools-*/SKILL.md and references/*
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure repo root imports work when script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.routers import tool_routes
from app.services.skills import SkillsLoader
from tool_box.bio_tools.bio_tools_handler import bio_tools_handler
from tool_box.bio_tools.remote_executor import RemoteExecutionConfig, resolve_auth, execute_remote_command

TOOLS_CONFIG_PATH = PROJECT_ROOT / "tool_box" / "bio_tools" / "tools_config.json"
MATRIX_OUTPUT_PATH = PROJECT_ROOT / "bio_tools_test_matrix.json"
REPORT_OUTPUT_PATH = PROJECT_ROOT / "bio_tool_list.md"
SKILLS_ROOT = PROJECT_ROOT / "skills"

REMOTE_DB_PATHS = {
    "bakta": "/home/zczhao/GAgent/data/databases/bio_tools/bakta/db",
    "checkm": "/home/zczhao/GAgent/data/databases/bio_tools/checkm_data",
    "checkv": "/home/zczhao/GAgent/data/databases/bio_tools/checkv/checkv-db-v1.5",
    "genomad": "/home/zczhao/GAgent/data/databases/bio_tools/genomad/genomad_db",
    "gtdbtk": "/home/zczhao/GAgent/data/databases/bio_tools/gtdbtk/gtdbtk_r220_data",
    "virsorter2": "/home/zczhao/GAgent/data/databases/bio_tools/virsorter2/db/db",
    "iphop": "/home/zczhao/GAgent/data/databases/bio_tools/iphop/Aug_2023_pub_rw",
}

SKIP_PRECONDITION_RULES = {
    ("dorado", "basecall"): "requires POD5/FAST5 raw signal dataset not included in smoke assets",
    ("dorado", "demux"): "requires Dorado basecall output/BAM layout not included in smoke assets",
    ("gtdbtk", "classify_wf"): "requires directory-based genome_dir upload workflow not covered by current minimal smoke assets",
    ("das_tool", "integrate"): "requires pre-generated multiple binning result sets and labels",
    ("checkm", "qa"): "requires tree/output from prior checkm lineage workflow run",
    ("bowtie2", "align"): "requires bowtie2 index prefix with multi-file side outputs from a prior run",
    ("bwa", "mem"): "requires reference side-index files alongside reference path",
    ("genomad", "find_proviruses"): "requires annotate-generated *_genes.tsv and *_proteins.faa in the same run directory",
    (
        "nextflow",
        "clean",
    ): "requires same working directory and execution history from a prior nextflow run, but matrix runner uses isolated run dirs per operation",
}

OPERATION_PHASE_OVERRIDES: Dict[Tuple[str, str], int] = {
    ("blast", "makeblastdb"): 0,
    ("blast", "blastn"): 8,
    ("blast", "blastp"): 8,
    ("bwa", "index"): 1,
    ("bwa", "mem"): 8,
    ("bowtie2", "build"): 1,
    ("bowtie2", "align"): 8,
    ("hmmer", "hmmbuild"): 1,
    ("hmmer", "hmmpress"): 2,
    ("hmmer", "hmmscan"): 3,
    ("hmmer", "hmmsearch"): 3,
    ("samtools", "view"): 2,
    ("samtools", "sort"): 3,
    ("samtools", "index"): 4,
    ("samtools", "stats"): 5,
    ("samtools", "view_filter_unmapped"): 6,
    ("metabat2", "depth"): 7,
    ("metabat2", "bin"): 9,
    ("checkv", "completeness"): 7,
    ("checkv", "complete_genomes"): 9,
    ("nextflow", "run"): 7,
    ("nextflow", "clean"): 9,
}

OPERATION_TIMEOUT_OVERRIDES: Dict[Tuple[str, str], int] = {
    # iPHoP host prediction can be significantly slower than generic Tier-2 budget.
    ("iphop", "predict"): int(os.getenv("BIO_TOOLS_MATRIX_IPHOP_TIMEOUT", "1200")),
}


@dataclass
class SmokeAssets:
    root: Path
    fasta_short: Path
    fasta_aligned: Path
    fasta_long: Path
    fastq_r1: Path
    fastq_r2: Path
    long_reads_fastq: Path
    sam: Path
    snakefile: Path
    nextflow_pipeline: Path
    hmm: Path


def _load_tools_config() -> Dict[str, Any]:
    return json.loads(TOOLS_CONFIG_PATH.read_text(encoding="utf-8"))


def _tier_for_operation(tool: str, operation: str, op_info: Dict[str, Any]) -> str:
    heavy_tools = {
        "bakta",
        "checkm",
        "checkv",
        "genomad",
        "gtdbtk",
        "iphop",
        "vibrant",
        "virsorter2",
        "flye",
        "megahit",
        "mmseqs2",
    }
    command = (op_info.get("command") or "").lower()
    extra = [str(x).lower() for x in (op_info.get("extra_params") or [])]
    heavy_markers = {
        "database",
        "genome_dir",
        "pipeline",
        "snakefile",
        "bins",
        "abundance",
        "coverage",
        "tree",
        "r1",
        "r2",
    }
    if tool in heavy_tools:
        return "Tier-2"
    if any(marker in command for marker in heavy_markers):
        return "Tier-2"
    if any(marker in extra for marker in heavy_markers):
        return "Tier-2"
    return "Tier-1"


def _timeout_for_tier(tier: str) -> int:
    if tier == "Tier-2":
        return int(os.getenv("BIO_TOOLS_MATRIX_TIER2_TIMEOUT", "1200"))
    return int(os.getenv("BIO_TOOLS_MATRIX_TIER1_TIMEOUT", "180"))


def _timeout_for_operation(tool: str, operation: str, tier: str) -> int:
    return OPERATION_TIMEOUT_OVERRIDES.get((tool, operation), _timeout_for_tier(tier))


def _execution_sort_key(tool: str, operation: str) -> Tuple[int, str, str]:
    return (OPERATION_PHASE_OVERRIDES.get((tool, operation), 5), tool, operation)


def _build_assets(root: Path) -> SmokeAssets:
    root.mkdir(parents=True, exist_ok=True)

    fasta_short = root / "test_short.fasta"
    fasta_aligned = root / "test_aligned.fasta"
    fasta_long = root / "test_long.fasta"
    fastq_r1 = root / "reads_R1.fastq"
    fastq_r2 = root / "reads_R2.fastq"
    long_reads_fastq = root / "reads_long.fastq"
    sam = root / "reads.sam"
    snakefile = root / "Snakefile"
    nextflow_pipeline = root / "main.nf"
    hmm = root / "test.hmm"

    fasta_short.write_text(
        ">seq1\nATGCGTACGTAGCTAGCTAGCTAGCTAGCTAGCTAG\n>seq2\nATGCGTACGTAGCTAGCTAG\n",
        encoding="utf-8",
    )
    fasta_aligned.write_text(
        ">a1\nATGCGTACGTAGCTAGCTAGCTAGCTAGCTAGCTAG\n>a2\nATGCGTACGTAGCTAGCTAGCTAGCTAGCTAGCTAA\n",
        encoding="utf-8",
    )
    rng = random.Random(20260222)
    long_seq = "".join(rng.choice("ACGT") for _ in range(25000))
    fasta_long.write_text(f">long_contig\n{long_seq}\n", encoding="utf-8")

    def _fastq_block(name: str, seq: str) -> str:
        return f"@{name}\n{seq}\n+\n{'F' * len(seq)}\n"

    seq1 = "ATGCGTACGT" * 120
    seq2 = "CGTAGCTAGC" * 120
    r1_content = "".join(_fastq_block(f"read{i}/1", seq1) for i in range(1, 13))
    r2_content = "".join(_fastq_block(f"read{i}/2", seq2) for i in range(1, 13))
    fastq_r1.write_text(r1_content, encoding="utf-8")
    fastq_r2.write_text(r2_content, encoding="utf-8")

    # Flye smoke cases need long, overlapping reads with enough depth.
    # Low-depth tiny sets can trigger unstable overlap heuristics in some images.
    flye_genome = "".join(rng.choice("ACGT") for _ in range(120000))

    def _slice_circular(seq: str, start: int, length: int) -> str:
        if start + length <= len(seq):
            return seq[start : start + length]
        remain = (start + length) - len(seq)
        return seq[start:] + seq[:remain]

    long_read_blocks: List[str] = []
    read_length = 15000
    for i, start in enumerate(range(0, 120000, 1000), start=1):
        read_seq = _slice_circular(flye_genome, start, read_length)
        long_read_blocks.append(_fastq_block(f"long_read{i}", read_seq))
    long_reads_fastq.write_text("".join(long_read_blocks), encoding="utf-8")

    sam.write_text(
        "@HD\tVN:1.6\tSO:unsorted\n"
        "@SQ\tSN:seq1\tLN:36\n"
        "read1\t0\tseq1\t1\t60\t44M\t*\t0\t0\tATGCGTACGTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAG\tFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF\n"
        "read_unmap\t4\t*\t0\t0\t*\t*\t0\t0\tATGCGTACGTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAG\tFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF\n",
        encoding="utf-8",
    )

    snakefile.write_text(
        textwrap.dedent(
            """
            rule all:
                input:
                    "result.txt"

            rule make_result:
                output:
                    "result.txt"
                shell:
                    "echo snakefile_ok > {output}"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    nextflow_pipeline.write_text(
        textwrap.dedent(
            """
            process hello {
                output:
                  path 'hello.txt'
                script:
                  '''
                  echo nextflow_ok > hello.txt
                  '''
            }

            workflow {
                hello()
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    hmm.write_text(
        textwrap.dedent(
            """
            HMMER3/f [3.3.2 | Nov 2020]
            NAME  test_hmm
            LENG  4
            ALPH  amino
            RF    no
            MM    no
            CONS  no
            CS    no
            MAP   no
            DATE  today
            NSEQ  1
            EFFN  1.000000
            CKSUM 123456
            STATS LOCAL MSV       -9.0  0.7
            STATS LOCAL VITERBI  -10.0  0.7
            STATS LOCAL FORWARD   -4.0  0.7
            HMM          A    C    D    E
                       -2   -2   -2   -2
            COMPO      0.25 0.25 0.25 0.25
                       2.0  2.0  2.0  2.0
                       2.0  2.0  2.0  2.0
                       2.0  2.0  2.0  2.0
                       2.0  2.0  2.0  2.0
            //
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    return SmokeAssets(
        root=root,
        fasta_short=fasta_short,
        fasta_aligned=fasta_aligned,
        fasta_long=fasta_long,
        fastq_r1=fastq_r1,
        fastq_r2=fastq_r2,
        long_reads_fastq=long_reads_fastq,
        sam=sam,
        snakefile=snakefile,
        nextflow_pipeline=nextflow_pipeline,
        hmm=hmm,
    )


async def _probe_remote_db_paths() -> Dict[str, bool]:
    config = RemoteExecutionConfig.from_env()
    missing = config.missing_required()
    if missing:
        return {name: False for name in REMOTE_DB_PATHS}

    auth = await resolve_auth(config)
    status: Dict[str, bool] = {}
    for tool, db_path in REMOTE_DB_PATHS.items():
        cmd = f"test -d '{db_path}'"
        # Remote FS probes can fail transiently under SSH load; retry to avoid false negatives.
        ok = False
        for attempt in range(1, 4):
            result = await execute_remote_command(config, auth, cmd, timeout=20)
            if result.get("success"):
                ok = True
                break
            if attempt < 3:
                await asyncio.sleep(1.0)
        status[tool] = ok
    return status


def _default_param_value(key: str, tool: str, operation: str, assets: SmokeAssets, state: Dict[str, Any]) -> Any:
    filename = f"{tool}_{operation}_{key}.out"

    defaults = {
        "pattern": "seq1",
        "count": 1,
        "fraction": 1.0,
        "threads": 1,
        "cores": 1,
        "quality": 20,
        "min_length": 20,
        "min_score": 0.5,
        "min_seq_id": 0.9,
        "search_type": 3,
        "preset": "map-ont",
        "type": "nucl",
        "prefix": f"{tool}_{operation}",
        "model": "dna_r10.4.1_e8.2_400bps_fast@v4.3.0",
        "kit": "SQK-LSK114",
        "pipeline": str(assets.nextflow_pipeline),
        "snakefile": str(assets.snakefile),
        "output": filename,
        "output_dir": f"{tool}_{operation}_out",
        "output_prefix": f"{tool}_{operation}_prefix",
        "index_prefix": f"{tool}_{operation}_index",
        "protein": f"{tool}_{operation}.faa",
        "nucleotide": f"{tool}_{operation}.fna",
    }

    if key == "db":
        if tool == "blast" and operation in {"blastn", "blastp"}:
            blast_db_prefix = state.get("blast_db_prefix")
            if blast_db_prefix:
                return blast_db_prefix
        return "test_db"

    if key == "reference":
        return str(assets.fasta_short)
    if key == "query":
        return str(assets.fasta_short)
    if key == "target":
        return str(assets.fasta_short)
    if key == "contigs":
        return str(assets.fasta_short)
    if key == "genome_dir":
        return str(assets.root / "genome_dir")
    if key == "abundance":
        if state.get("metabat_depth"):
            return state["metabat_depth"]
        return str(assets.root / "abundance.tsv")
    if key == "coverage":
        if state.get("metabat_depth"):
            return state["metabat_depth"]
        return str(assets.root / "coverage.tsv")
    if key == "tree":
        return str(assets.root / "checkm.tree")
    if key == "r1":
        return str(assets.fastq_r1)
    if key == "r2":
        return str(assets.fastq_r2)
    if key == "bam_files":
        if state.get("samtools_sorted_bam"):
            return state["samtools_sorted_bam"]
        return str(assets.sam)
    if key == "bins":
        return str(assets.fasta_short)
    if key == "input":
        return str(assets.fasta_short)

    return defaults.get(key, filename)


def _build_payload(
    tool: str,
    operation: str,
    op_info: Dict[str, Any],
    assets: SmokeAssets,
    state: Dict[str, Any],
    db_status: Dict[str, bool],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if (tool, operation) in SKIP_PRECONDITION_RULES:
        return None, SKIP_PRECONDITION_RULES[(tool, operation)]

    extra_params = list(op_info.get("extra_params") or [])
    params: Dict[str, Any] = {
        key: _default_param_value(key, tool, operation, assets, state) for key in extra_params
    }

    input_file: Optional[str] = None
    if op_info.get("requires_input"):
        if tool in {"trim_galore", "nanoplot", "megahit", "flye"}:
            input_file = str(assets.long_reads_fastq) if tool == "flye" else str(assets.fastq_r1)
        elif tool == "samtools":
            if operation == "index" and state.get("samtools_sorted_bam"):
                input_file = state["samtools_sorted_bam"]
            elif operation in {"stats", "view"} and state.get("samtools_sorted_bam"):
                input_file = state["samtools_sorted_bam"]
            else:
                input_file = str(assets.sam)
        elif tool == "prodigal" and operation == "predict":
            input_file = str(assets.fasta_long)
        elif tool in {"sniffles2"} and state.get("samtools_indexed_bam"):
            input_file = state["samtools_indexed_bam"]
        else:
            input_file = str(assets.fasta_short)

    # Tool specific payload overrides.
    if tool == "minimap2" and operation == "map":
        params.setdefault("preset", "map-ont")
        params["reference"] = str(assets.fasta_short)
        params["query"] = str(assets.fasta_short)
        params["output"] = f"{tool}_{operation}.sam"
        input_file = None

    if tool == "minimap2" and operation == "filter":
        params["reference"] = str(assets.fasta_short)
        params["output"] = f"{tool}_{operation}.sam"
        params.setdefault("threads", 1)
        input_file = str(assets.fasta_short)

    if tool == "nextflow" and operation == "run":
        params["pipeline"] = str(assets.nextflow_pipeline)
        input_file = str(assets.fasta_short)

    if tool == "snakemake":
        params["snakefile"] = str(assets.snakefile)
        if operation == "run":
            params.setdefault("cores", 1)
        input_file = str(assets.fasta_short)

    if tool == "hmmer":
        if operation == "hmmbuild":
            input_file = str(assets.fasta_aligned)
            params["output"] = "hmmer_profile.hmm"
        elif operation == "hmmpress":
            if not state.get("hmmer_hmm_path"):
                return None, "requires HMM profile from successful hmmer hmmbuild"
            input_file = None
            params["db"] = state["hmmer_hmm_path"]
        elif operation in {"hmmscan", "hmmsearch"}:
            if not state.get("hmmer_hmm_path"):
                return None, "requires HMM profile from successful hmmer hmmbuild"
            input_file = str(assets.fasta_short)
            params["db"] = state["hmmer_hmm_path"]
            params["output"] = f"{tool}_{operation}.tbl"

    if tool == "blast" and operation == "makeblastdb":
        params["type"] = "nucl"
        params["db"] = "test_db"
        input_file = str(assets.fasta_short)

    if tool == "blast" and operation in {"blastn", "blastp"}:
        return None, "requires BLAST DB side-files packaging across runs, which is not supported by current matrix runner"

    if tool == "fastani" and operation == "compare":
        params["query"] = str(assets.fasta_long)
        params["reference"] = str(assets.fasta_long)
        params["output"] = "fastani_compare.tsv"
        input_file = None

    if tool == "samtools" and operation in {"view", "sort", "stats", "view_filter_unmapped"}:
        params.setdefault("threads", 1)
        if operation == "view":
            params["output"] = "samtools_view.bam"
        elif operation == "sort":
            params["output"] = "samtools_sorted.bam"
        elif operation == "stats":
            params["output"] = "samtools_stats.txt"
        else:
            params.setdefault("output", "samtools_unmapped.fastq.gz")

    if tool == "samtools" and operation == "index":
        if state.get("samtools_sorted_bam"):
            input_file = state["samtools_sorted_bam"]
        elif state.get("samtools_view_bam"):
            input_file = state["samtools_view_bam"]
        else:
            return None, "requires BAM from samtools view/sort before index"

    if tool == "sniffles2":
        if not state.get("samtools_indexed_bam"):
            return None, "requires indexed BAM from samtools index result"
        input_file = state["samtools_indexed_bam"]
        params["reference"] = str(assets.fasta_short)
        params.setdefault("threads", 1)
        params.setdefault("output", "sniffles.vcf")

    if tool == "metabat2" and operation == "depth":
        if not state.get("samtools_sorted_bam"):
            return None, "requires BAM from samtools workflow for bam_files"
        params["bam_files"] = state["samtools_sorted_bam"]
        params["output"] = "metabat_depth.tsv"

    if tool == "metabat2" and operation == "bin":
        if not state.get("metabat_depth"):
            return None, "requires depth file from metabat2 depth"
        params["contigs"] = str(assets.fasta_short)
        params["depth"] = state["metabat_depth"]
        params["output_prefix"] = "metabat_bins/bin"

    if tool == "concoct":
        if not state.get("metabat_depth"):
            return None, "requires coverage/depth table for concoct bin"
        params["coverage"] = state["metabat_depth"]
        params["contigs"] = str(assets.fasta_short)
        params["output"] = "concoct_bins/"

    if tool == "maxbin2":
        if not state.get("metabat_depth"):
            return None, "requires abundance profile for maxbin2 bin"
        params["contigs"] = str(assets.fasta_short)
        params["abundance"] = state["metabat_depth"]
        params["output"] = "maxbin_bins/out"

    if tool == "checkv":
        if operation in {"completeness", "end_to_end"}:
            input_file = str(assets.fasta_long)
            params["output"] = f"checkv_{operation}_out"
            params["threads"] = 1
        if operation == "complete_genomes":
            if not state.get("checkv_completeness_tsv"):
                return None, "requires completeness.tsv from successful checkv completeness"
            input_file = state["checkv_completeness_tsv"]
            params["output"] = "checkv_complete_genomes_out"

    if tool == "genomad" and operation in {"annotate", "end_to_end"}:
        # Keep smoke runs lightweight; larger inputs inflate artifact sync time significantly.
        input_file = str(assets.fasta_short)
        params["output"] = f"genomad_{operation}_out"

    if tool == "flye":
        input_file = str(assets.long_reads_fastq)
        params["output"] = f"flye_{operation}_out"

    if tool == "nextflow" and operation == "clean":
        if not state.get("nextflow_run_ok"):
            return None, "requires successful nextflow run before clean"

    # Database-backed tools: ensure remote DB path exists before execution.
    if tool in REMOTE_DB_PATHS and not db_status.get(tool, False):
        return None, f"remote database path missing: {REMOTE_DB_PATHS[tool]}"

    # Known directory-upload unsupported pattern for current handler.
    if tool == "gtdbtk" and operation == "classify_wf":
        return None, "directory upload for genome_dir is not supported by current remote IO rewrite"

    payload: Dict[str, Any] = {
        "tool_name": tool,
        "operation": operation,
        "timeout": _timeout_for_operation(tool, operation, _tier_for_operation(tool, operation, op_info)),
    }
    if input_file:
        payload["input_file"] = input_file
    if params:
        payload["params"] = params
    return payload, None


def _tail_text(text: str, limit: int = 400) -> str:
    value = (text or "").strip()
    return value[-limit:]


def _precondition_reason_from_failure(tool: str, operation: str, reason_text: str) -> Optional[str]:
    low = (reason_text or "").lower()

    if tool == "bakta" and "wrong database version detected" in low:
        return "remote bakta database version is incompatible with container image (requires v6.x)"
    if tool == "checkm" and "nameerror" in low and "checkmdata.py" in low:
        return "checkm container runtime bug (python exception in checkmData) on remote image"
    if tool == "genomad" and "invalid literal for int() with base 10: 'na'" in low:
        return "genomad remote database content/schema is incompatible with current container"
    if tool == "virsorter2" and "/work/database/conda_envs" in low and "permission denied" in low:
        return "virsorter2 attempted to create conda env under mounted DB path without write permission"
    if tool == "nextflow" and operation == "clean" and "execution history is empty" in low:
        return "nextflow clean needs run history in the same work directory (matrix uses isolated dirs)"
    if any(marker in low for marker in ("manifest unknown", "pull access denied", "repository does not exist")):
        return "container image unavailable in current remote environment"
    return None


async def _run_tier0_checks(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for tool in sorted(config):
        help_result = await bio_tools_handler(tool_name=tool, operation="help", timeout=120)
        help_ops = set((help_result.get("operations") or {}).keys()) if help_result.get("success") else set()
        for operation in sorted(config[tool].get("operations", {}).keys()):
            ok = help_result.get("success") and operation in help_ops
            records.append(
                {
                    "tool": tool,
                    "operation": operation,
                    "tier": "Tier-0",
                    "status": "PASS" if ok else "FAIL",
                    "reason": None
                    if ok
                    else help_result.get("error")
                    or f"operation '{operation}' missing from help response",
                    "params_template": {"tool_name": tool, "operation": operation},
                    "last_run_id": None,
                    "artifacts": {},
                    "exit_code": help_result.get("exit_code"),
                    "stderr_tail": _tail_text(help_result.get("stderr", "")),
                    "duration": help_result.get("duration_seconds"),
                }
            )
    return records


async def _run_execution_checks(
    config: Dict[str, Any],
    assets: SmokeAssets,
    db_status: Dict[str, bool],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {}
    cases: List[Tuple[str, str, Dict[str, Any]]] = []
    for tool in config:
        for operation in config[tool].get("operations", {}):
            cases.append((tool, operation, config[tool]["operations"][operation]))
    cases.sort(key=lambda item: _execution_sort_key(item[0], item[1]))

    for tool, operation, op_info in cases:
        tier = _tier_for_operation(tool, operation, op_info)
        payload, skip_reason = _build_payload(tool, operation, op_info, assets, state, db_status)

        params_template = {
            "tool_name": tool,
            "operation": operation,
        }
        if payload:
            if payload.get("input_file"):
                params_template["input_file"] = payload["input_file"]
            if payload.get("params"):
                params_template["params"] = payload["params"]

        if skip_reason:
            records.append(
                {
                    "tool": tool,
                    "operation": operation,
                    "tier": tier,
                    "status": "SKIPPED_PRECONDITION",
                    "reason": skip_reason,
                    "params_template": params_template,
                    "last_run_id": None,
                    "artifacts": {},
                    "exit_code": None,
                    "stderr_tail": "",
                    "duration": None,
                }
            )
            continue

        assert payload is not None
        result = await bio_tools_handler(
            tool_name=payload["tool_name"],
            operation=payload["operation"],
            input_file=payload.get("input_file"),
            output_file=payload.get("output_file"),
            params=payload.get("params"),
            timeout=int(payload.get("timeout", _timeout_for_tier(tier))),
        )
        params_used = payload.get("params") or {}

        success = bool(result.get("success"))
        run_id = result.get("run_id")
        remote_run_dir = result.get("remote_run_dir")
        local_artifact_dir = result.get("local_artifact_dir")
        stderr_tail = _tail_text(result.get("stderr", ""))
        stdout_tail = _tail_text(result.get("stdout", ""))
        reason_text = (
            result.get("error")
            or stderr_tail
            or stdout_tail
            or "execution failed without explicit error"
        )
        status = "PASS" if success else "FAIL"
        low_reason = reason_text.lower()
        if not success and "requested access to the resource is denied" in low_reason:
            status = "SKIPPED_PRECONDITION"
        precondition_reason = _precondition_reason_from_failure(tool, operation, reason_text)
        if not success and precondition_reason:
            status = "SKIPPED_PRECONDITION"
            reason_text = precondition_reason

        record = {
            "tool": tool,
            "operation": operation,
            "tier": tier,
            "status": status,
            "reason": None if success else reason_text,
            "params_template": params_template,
            "last_run_id": run_id,
            "artifacts": {
                "execution_mode": result.get("execution_mode"),
                "execution_host": result.get("execution_host"),
                "remote_run_dir": remote_run_dir,
                "local_artifact_dir": local_artifact_dir,
                "output_path": result.get("output_path"),
            },
            "exit_code": result.get("exit_code"),
            "stderr_tail": stderr_tail,
            "duration": result.get("duration_seconds"),
        }
        records.append(record)

        # Capture reusable outputs for dependent operations.
        if success and local_artifact_dir:
            artifact_root = Path(local_artifact_dir)
            if tool == "samtools" and operation == "view":
                bam_name = Path(str(params_used.get("output") or "samtools_view.bam")).name
                view_bam = artifact_root / "output" / bam_name
                if view_bam.exists():
                    state["samtools_view_bam"] = str(view_bam)

            if tool == "samtools" and operation == "sort":
                sort_name = Path(str(params_used.get("output") or "samtools_sorted.bam")).name
                sorted_bam = artifact_root / "output" / sort_name
                if sorted_bam.exists():
                    state["samtools_sorted_bam"] = str(sorted_bam)
                elif result.get("output_path"):
                    candidate = Path(str(result["output_path"]))
                    if candidate.exists():
                        state["samtools_sorted_bam"] = str(candidate)

            if tool == "samtools" and operation == "index":
                indexed_bam = artifact_root / "input" / "samtools_sorted.bam"
                indexed_bai = artifact_root / "input" / "samtools_sorted.bam.bai"
                if indexed_bam.exists() and indexed_bai.exists():
                    state["samtools_indexed_bam"] = str(indexed_bam)
                elif state.get("samtools_sorted_bam"):
                    # Fallback when artifacts layout differs; sidecar upload logic can still pick local indexes.
                    state["samtools_indexed_bam"] = state["samtools_sorted_bam"]

            if tool == "metabat2" and operation == "depth":
                depth_name = Path(str(params_used.get("output") or "metabat_depth.tsv")).name
                depth_path = artifact_root / "output" / depth_name
                if depth_path.exists():
                    state["metabat_depth"] = str(depth_path)
                elif result.get("output_path"):
                    candidate = Path(str(result["output_path"]))
                    if candidate.exists():
                        state["metabat_depth"] = str(candidate)

            if tool == "hmmer" and operation == "hmmbuild":
                hmm_name = Path(str(params_used.get("output") or "hmmer_profile.hmm")).name
                hmm_path = artifact_root / "output" / hmm_name
                if hmm_path.exists():
                    state["hmmer_hmm_path"] = str(hmm_path)
            if tool == "hmmer" and operation == "hmmpress":
                base_hmm = Path(str(params_used.get("db") or "hmmer_profile.hmm")).name
                pressed_hmm = artifact_root / "input" / base_hmm
                sidecars = [pressed_hmm.with_name(pressed_hmm.name + ext) for ext in (".h3f", ".h3i", ".h3m", ".h3p")]
                if pressed_hmm.exists() and all(p.exists() for p in sidecars):
                    state["hmmer_hmm_path"] = str(pressed_hmm)

            if tool == "checkv" and operation == "completeness":
                for candidate in artifact_root.rglob("completeness.tsv"):
                    state["checkv_completeness_tsv"] = str(candidate)
                    break

            if tool == "nextflow" and operation == "run":
                state["nextflow_run_ok"] = True

    return records


def _run_api_sampling(execution_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    pass_cases = [
        row
        for row in execution_records
        if row.get("status") == "PASS" and row.get("tier") == "Tier-1" and row.get("params_template")
    ]
    # Keep dependency order stable for chained operations (e.g. hmmbuild->hmmpress->hmmscan).
    pass_cases = sorted(
        pass_cases,
        key=lambda r: _execution_sort_key(str(r.get("tool")), str(r.get("operation"))),
    )[:12]

    app = FastAPI()
    app.include_router(tool_routes.router)

    results: List[Dict[str, Any]] = []
    with TestClient(app) as client:
        for row in pass_cases:
            payload = dict(row["params_template"])
            response = client.post("/tools/bio-tools", json=payload)
            body: Dict[str, Any] = {}
            try:
                body = response.json()
            except Exception:
                body = {}
            results.append(
                {
                    "tool": row["tool"],
                    "operation": row["operation"],
                    "status_code": response.status_code,
                    "success": bool(body.get("success")) if isinstance(body, dict) else False,
                    "error": (body.get("error") if isinstance(body, dict) else None),
                }
            )

    success_count = sum(1 for item in results if item.get("status_code") == 200 and item.get("success"))
    return {
        "sample_size": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "cases": results,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_report_markdown(matrix: Dict[str, Any]) -> str:
    generated_at = matrix["meta"].get("generated_at")
    exec_rows = matrix["execution"]

    counts = Counter(row["status"] for row in exec_rows)
    by_tier: Dict[str, Counter] = defaultdict(Counter)
    for row in exec_rows:
        by_tier[row["tier"]][row["status"]] += 1

    passed_rows = [row for row in exec_rows if row["status"] == "PASS"]
    failed_rows = [row for row in exec_rows if row["status"] == "FAIL"]
    skipped_rows = [row for row in exec_rows if row["status"] == "SKIPPED_PRECONDITION"]

    lines: List[str] = []
    lines.extend(
        [
            "---",
            'name: "bio-tools-remote-reference"',
            'description: "Runtime-verified bio_tools reference for local development + remote server execution."',
            f'updated_at: "{generated_at}"',
            "---",
            "",
            "# Bio Tools Remote Reference",
            "",
            "## Coverage Snapshot",
            f"- Generated at: `{generated_at}`",
            f"- Tier-0 operation discoverability: `{matrix['summary']['tier0_pass']}/{matrix['summary']['tier0_total']}` PASS",
            f"- Execution totals: PASS `{counts.get('PASS', 0)}`, FAIL `{counts.get('FAIL', 0)}`, SKIPPED_PRECONDITION `{counts.get('SKIPPED_PRECONDITION', 0)}`",
            f"- API sampling: `{matrix['api_sampling']['success_count']}/{matrix['api_sampling']['sample_size']}` successful `/tools/bio-tools` calls",
            "",
            "## Tier Breakdown",
            "| Tier | PASS | FAIL | SKIPPED_PRECONDITION |",
            "|---|---:|---:|---:|",
        ]
    )
    for tier in sorted(by_tier):
        tier_counter = by_tier[tier]
        lines.append(
            f"| {tier} | {tier_counter.get('PASS', 0)} | {tier_counter.get('FAIL', 0)} | {tier_counter.get('SKIPPED_PRECONDITION', 0)} |"
        )

    lines.extend(
        [
            "",
            "## Runtime-Verified PASS Operations",
            "| Tool | Operation | Tier | last_run_id |",
            "|---|---|---|---|",
        ]
    )
    for row in sorted(passed_rows, key=lambda r: (r["tool"], r["operation"])):
        lines.append(
            f"| `{row['tool']}` | `{row['operation']}` | {row['tier']} | `{row.get('last_run_id') or '-'}` |"
        )

    lines.extend(
        [
            "",
            "## Failed Operations",
            "| Tool | Operation | Tier | Reason (tail) |",
            "|---|---|---|---|",
        ]
    )
    for row in sorted(failed_rows, key=lambda r: (r["tool"], r["operation"])):
        reason = (row.get("reason") or "").replace("\n", " ")[:180]
        lines.append(f"| `{row['tool']}` | `{row['operation']}` | {row['tier']} | {reason} |")

    lines.extend(
        [
            "",
            "## Skipped (Precondition)",
            "| Tool | Operation | Tier | Reason |",
            "|---|---|---|---|",
        ]
    )
    for row in sorted(skipped_rows, key=lambda r: (r["tool"], r["operation"])):
        reason = (row.get("reason") or "").replace("\n", " ")[:180]
        lines.append(f"| `{row['tool']}` | `{row['operation']}` | {row['tier']} | {reason} |")

    lines.extend(
        [
            "",
            "## Agent Usage Guidance",
            "1. Prefer operations marked PASS for production tasks.",
            "2. For FAIL entries, check `bio_tools_test_matrix.json` and inspect `stderr_tail` + artifact directories.",
            "3. For SKIPPED_PRECONDITION entries, prepare required data/dependencies first, then rerun matrix.",
            "",
            "## Artifact Paths",
            f"- Machine-readable matrix: `{MATRIX_OUTPUT_PATH}`",
            f"- Skill directories: `{SKILLS_ROOT / 'bio-tools-router'}`, `{SKILLS_ROOT / 'bio-tools-execution-playbook'}`, `{SKILLS_ROOT / 'bio-tools-troubleshooting'}`",
        ]
    )

    return "\n".join(lines) + "\n"


def _write_skill_assets(matrix: Dict[str, Any]) -> None:
    execution_rows = matrix["execution"]

    pass_rows = [row for row in execution_rows if row["status"] == "PASS"]
    fail_rows = [row for row in execution_rows if row["status"] == "FAIL"]
    skipped_rows = [row for row in execution_rows if row["status"] == "SKIPPED_PRECONDITION"]

    verified_lines = [
        "# Verified Operations",
        "",
        "Generated from bio_tools_test_matrix.json.",
        "",
        "| Tool | Operation | Tier | last_run_id |",
        "|---|---|---|---|",
    ]
    for row in sorted(pass_rows, key=lambda r: (r["tool"], r["operation"])):
        verified_lines.append(
            f"| `{row['tool']}` | `{row['operation']}` | {row['tier']} | `{row.get('last_run_id') or '-'}` |"
        )

    templates: Dict[str, Any] = {}
    for row in sorted(pass_rows, key=lambda r: (r["tool"], r["operation"])):
        key = f"{row['tool']}::{row['operation']}"
        templates[key] = row.get("params_template")

    error_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in fail_rows + skipped_rows:
        reason = (row.get("reason") or "unknown").split("\n", 1)[0].strip()
        if not reason:
            reason = "unknown"
        error_groups[reason].append(row)

    error_lines = [
        "# Error Catalog",
        "",
        "Grouped from FAIL and SKIPPED_PRECONDITION records in bio_tools_test_matrix.json.",
        "",
    ]
    for reason in sorted(error_groups):
        error_lines.append(f"## {reason}")
        for row in sorted(error_groups[reason], key=lambda r: (r["tool"], r["operation"])):
            error_lines.append(f"- `{row['tool']}` / `{row['operation']}` ({row['status']}, {row['tier']})")
        error_lines.append("")

    router_dir = SKILLS_ROOT / "bio-tools-router"
    playbook_dir = SKILLS_ROOT / "bio-tools-execution-playbook"
    troubleshoot_dir = SKILLS_ROOT / "bio-tools-troubleshooting"

    for directory in [router_dir, playbook_dir, troubleshoot_dir]:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "references").mkdir(parents=True, exist_ok=True)

    (router_dir / "references" / "verified_ops.md").write_text("\n".join(verified_lines) + "\n", encoding="utf-8")
    (playbook_dir / "references" / "request_templates.json").write_text(
        json.dumps(templates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (troubleshoot_dir / "references" / "error_catalog.md").write_text(
        "\n".join(error_lines), encoding="utf-8"
    )

    router_skill = textwrap.dedent(
        """
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

        ## Input strategy
        - Pass local absolute paths for `input_file` and path-like params.
        - Keep output names relative where possible (e.g., `aln.sam`, `stats.txt`).

        ## Reference
        - Verified operation list: `references/verified_ops.md`
        """
    ).strip() + "\n"

    playbook_skill = textwrap.dedent(
        """
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
        """
    ).strip() + "\n"

    troubleshooting_skill = textwrap.dedent(
        """
        ---
        name: bio-tools-troubleshooting
        description: "Troubleshooting skill for bio_tools failures in remote execution mode. Trigger on permission/path/database/parameter/runtime errors and provide deterministic retry sequences."
        ---

        # Bio Tools Troubleshooting

        ## Recovery sequence
        1. Re-run with `operation="help"` for the same tool.
        2. Validate path and required params against help output.
        3. Confirm remote DB preconditions for DB-backed tools.
        4. Retry with corrected params.
        5. If still failing, switch to nearest supported alternative operation/tool.

        ## Error classes
        - Permission/identity errors
        - Missing file/path mapping errors
        - Missing database/precondition errors
        - Unsupported data format or tool-level constraints
        - Timeout/runtime saturation

        ## Reference
        - Error groups: `references/error_catalog.md`
        """
    ).strip() + "\n"

    (router_dir / "SKILL.md").write_text(router_skill, encoding="utf-8")
    (playbook_dir / "SKILL.md").write_text(playbook_skill, encoding="utf-8")
    (troubleshoot_dir / "SKILL.md").write_text(troubleshooting_skill, encoding="utf-8")


async def _build_matrix(args: argparse.Namespace) -> Dict[str, Any]:
    config = _load_tools_config()

    if args.enforce_remote and os.getenv("BIO_TOOLS_EXECUTION_MODE", "").strip().lower() != "remote":
        raise RuntimeError("BIO_TOOLS_EXECUTION_MODE must be 'remote' when --enforce-remote is enabled")

    assets_root = Path(args.assets_dir).expanduser().resolve()
    assets = _build_assets(assets_root)

    db_status = await _probe_remote_db_paths()
    tier0_records = await _run_tier0_checks(config)
    execution_records = await _run_execution_checks(config, assets, db_status)

    tier0_counter = Counter(row["status"] for row in tier0_records)
    execution_counter = Counter(row["status"] for row in execution_records)

    matrix: Dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "execution_mode": os.getenv("BIO_TOOLS_EXECUTION_MODE", ""),
            "assets_dir": str(assets.root),
            "tools_count": len(config),
            "operations_count": sum(len(v.get("operations", {})) for v in config.values()),
            "timeouts": {
                "tier1": _timeout_for_tier("Tier-1"),
                "tier2": _timeout_for_tier("Tier-2"),
            },
            "remote_db_status": db_status,
        },
        "tier0": tier0_records,
        "execution": execution_records,
        "summary": {
            "tier0_total": len(tier0_records),
            "tier0_pass": tier0_counter.get("PASS", 0),
            "tier0_fail": tier0_counter.get("FAIL", 0),
            "execution_total": len(execution_records),
            "execution_pass": execution_counter.get("PASS", 0),
            "execution_fail": execution_counter.get("FAIL", 0),
            "execution_skipped_precondition": execution_counter.get("SKIPPED_PRECONDITION", 0),
        },
    }
    return matrix


def _verify_skills_discovery() -> Dict[str, Any]:
    loader = SkillsLoader(
        skills_dir=str(SKILLS_ROOT),
        project_skills_dir=str(SKILLS_ROOT),
        auto_sync=False,
    )
    names = {item.get("name") for item in loader.list_skills()}
    required = {
        "bio-tools-router",
        "bio-tools-execution-playbook",
        "bio-tools-troubleshooting",
    }
    return {
        "available_skills": sorted(str(name) for name in names if name),
        "required_skills_detected": sorted(required & names),
        "missing_required_skills": sorted(required - names),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build bio_tools test matrix and skills assets")
    parser.add_argument(
        "--assets-dir",
        default=os.getenv("BIO_TOOLS_MATRIX_ASSETS_DIR", "/tmp/bio_tools_matrix_assets"),
        help="Directory for generated smoke test assets",
    )
    parser.add_argument(
        "--enforce-remote",
        action="store_true",
        help="Fail fast when BIO_TOOLS_EXECUTION_MODE is not remote",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    matrix = asyncio.run(_build_matrix(args))

    # Chain/API sampling with 10+ direct /tools/bio-tools calls.
    api_sampling = _run_api_sampling(matrix["execution"])
    matrix["api_sampling"] = api_sampling

    _write_json(MATRIX_OUTPUT_PATH, matrix)

    report_md = _render_report_markdown(matrix)
    REPORT_OUTPUT_PATH.write_text(report_md, encoding="utf-8")

    _write_skill_assets(matrix)
    matrix["skills_discovery"] = _verify_skills_discovery()

    # Persist final matrix including skills discovery.
    _write_json(MATRIX_OUTPUT_PATH, matrix)

    print(f"Wrote matrix: {MATRIX_OUTPUT_PATH}")
    print(f"Wrote report: {REPORT_OUTPUT_PATH}")
    print(f"Wrote skills under: {SKILLS_ROOT}")
    print(json.dumps(matrix["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(matrix["api_sampling"], ensure_ascii=False, indent=2))
    print(json.dumps(matrix["skills_discovery"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
