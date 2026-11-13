"""PhageScope V2 catalog query and workspace materialization tool."""

from __future__ import annotations

from typing import Any

from .config import phagescope_data_enabled
from .handler import phagescope_data_handler
from .schema import DESCRIPTION, PARAMETERS_SCHEMA

phagescope_data_tool: dict[str, Any] = {
    "name": "phagescope_data",
    "description": DESCRIPTION,
    "category": "bioinformatics",
    "parameters_schema": PARAMETERS_SCHEMA,
    "handler": phagescope_data_handler,
    "tags": ["phage", "phagescope", "genomics", "proteins", "database", "fasta"],
    "examples": [
        "Search High-quality virulent Pseudomonas phages across PhageScope V2 and export metadata TSV",
        "Aggregate phage genome counts and length ranges by host_genus or dataset",
        "Search 12.6M protein families for endolysin/holin and materialize untruncated .faa for HMMER",
        "Materialize full .fasta, .faa, and .gff3 files of selected phages for CheckV/PhaBOX2/FastANI",
    ],
}

__all__ = [
    "DESCRIPTION",
    "PARAMETERS_SCHEMA",
    "phagescope_data_enabled",
    "phagescope_data_handler",
    "phagescope_data_tool",
]
