"""Ready-made pipelines a user can start from.

Each template must validate as it is, without uploads, so it can run straight away; the tests check that
and run every template through the engine.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Template:
    id: str
    title: Dict[str, str]
    description: Dict[str, str]
    graph: Dict[str, Any]


def _ref(node: str, port: str) -> Dict[str, str]:
    return {"node": node, "port": port}


def _phage(prefix: str, label: str, accession: str) -> Dict[str, Dict[str, Any]]:
    genome = _ref(prefix, "genome")
    return {
        prefix: {"type": "ncbi.genome", "label": label, "params": {"accessions": accession}},
        f"{prefix}_stats": {"type": "seqkit.stats", "label": label, "inputs": {"sequences": genome}},
        f"{prefix}_genes": {"type": "prodigal.predict", "label": label, "inputs": {"genome": genome}},
        f"{prefix}_genomad": {"type": "genomad.end_to_end", "label": label, "inputs": {"sequences": genome}},
        f"{prefix}_checkv": {"type": "checkv.end_to_end", "label": label, "inputs": {"sequences": genome}},
        f"{prefix}_lifestyle": {"type": "phabox2.phatyp", "label": label, "inputs": {"sequences": genome}},
    }


_LAMBDA_T7_REPORT = (
    "Compare phage lambda (NC_001416.1) with phage T7 (NC_001604.1). Cover genome length and GC content, "
    "the number of predicted genes, the geNomad virus call and taxonomy, CheckV completeness and quality, "
    "the PhaTYP lifestyle and whether it matches what is known about these two phages, and how similar the "
    "two genomes and proteomes are (FastANI, blastp, MMseqs2). Put the key numbers for both phages in one "
    "table. Say plainly when a result is missing or when two tools disagree."
)

_LAMBDA_T7_LAYOUT: Dict[str, Tuple[int, int]] = {
    "lambda": (0, 170),
    "lambda_stats": (300, 0),
    "lambda_genes": (300, 130),
    "lambda_genomad": (580, 0),
    "lambda_checkv": (580, 140),
    "lambda_lifestyle": (580, 280),
    "ani": (300, 480),
    "t7_protdb": (580, 560),
    "blastp": (860, 420),
    "mmseqs": (860, 580),
    "report": (1160, 500),
    "t7": (0, 830),
    "t7_genes": (300, 800),
    "t7_stats": (300, 980),
    "t7_genomad": (580, 760),
    "t7_checkv": (580, 900),
    "t7_lifestyle": (580, 1040),
}


def _lambda_t7() -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    nodes.update(_phage("lambda", "lambda", "NC_001416.1"))
    nodes.update(_phage("t7", "T7", "NC_001604.1"))
    nodes.update(
        {
            "ani": {
                "type": "fastani.compare",
                "label": "lambda vs T7",
                "inputs": {"query": _ref("lambda", "genome"), "reference": _ref("t7", "genome")},
            },
            "t7_protdb": {"type": "blast.makeblastdb_aa", "label": "T7", "inputs": {"proteins": _ref("t7_genes", "proteins")}},
            "blastp": {
                "type": "blast.blastp",
                "label": "lambda vs T7",
                "inputs": {"query": _ref("lambda_genes", "proteins"), "db": _ref("t7_protdb", "db")},
            },
            "mmseqs": {
                "type": "mmseqs2.protein_search",
                "label": "lambda vs T7",
                "inputs": {"query": _ref("lambda_genes", "proteins"), "target": _ref("t7_genes", "proteins")},
            },
            "report": {
                "type": "agent.report",
                "params": {"instructions": _LAMBDA_T7_REPORT, "language": "zh"},
                "inputs": {
                    "evidence": [
                        _ref("lambda_stats", "stats"),
                        _ref("t7_stats", "stats"),
                        _ref("lambda_genomad", "virus_summary"),
                        _ref("t7_genomad", "virus_summary"),
                        _ref("lambda_checkv", "quality"),
                        _ref("t7_checkv", "quality"),
                        _ref("lambda_lifestyle", "prediction"),
                        _ref("t7_lifestyle", "prediction"),
                        _ref("ani", "ani"),
                        _ref("blastp", "hits"),
                        _ref("mmseqs", "hits"),
                    ]
                },
            },
        }
    )
    layout = {node_id: {"x": x, "y": y} for node_id, (x, y) in _LAMBDA_T7_LAYOUT.items()}
    return {"schema": 1, "nodes": nodes, "layout": layout}


TEMPLATES: Tuple[Template, ...] = (
    Template(
        id="phage_lambda_t7",
        title={"en": "Phage basics: lambda vs T7", "zh": "噬菌体基础分析：lambda 对比 T7"},
        description={
            "en": (
                "Downloads lambda and T7 from NCBI, runs statistics, gene prediction, geNomad, CheckV and PhaTYP "
                "on each, compares them with FastANI, blastp and MMseqs2, and ends with an agent report. "
                "No upload needed."
            ),
            "zh": (
                "从 NCBI 下载 lambda 和 T7，分别做序列统计、基因预测、geNomad、CheckV 和 PhaTYP，"
                "再用 FastANI、blastp 和 MMseqs2 比较两者，最后由 Agent 写报告。无需上传文件。"
            ),
        },
        graph=_lambda_t7(),
    ),
)

_BY_ID: Dict[str, Template] = {template.id: template for template in TEMPLATES}


def graph_for(template_id: str) -> Optional[Dict[str, Any]]:
    """A fresh copy of a template's graph, or None for an unknown id."""
    template = _BY_ID.get(str(template_id or ""))
    return copy.deepcopy(template.graph) if template else None


def public_list() -> List[Dict[str, Any]]:
    return [
        {
            "id": template.id,
            "title": template.title,
            "description": template.description,
            "node_count": len(template.graph["nodes"]),
        }
        for template in TEMPLATES
    ]
