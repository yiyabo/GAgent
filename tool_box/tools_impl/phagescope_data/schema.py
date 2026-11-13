"""Schema definition for the phagescope_data tool."""

from __future__ import annotations

from typing import Any

OPERATIONS = (
    "overview",
    "datasets",
    "search_phages",
    "get_phage",
    "aggregate",
    "search_proteins",
    "family_members",
    "features",
    "structures",
    "materialize",
)

DESCRIPTION = (
    "PREFERRED read-only PhageScope V2 catalog query, aggregation, and untruncated sequence/annotation/structure "
    "materializer (2,576,926 phages across 27 datasets, 131.5M proteins, 12.61M protein families, 4,723 host species, "
    "7 functional feature tracks, and ESMFold/AFDB structures). "
    "Operations: "
    "(1) overview / datasets — catalog totals, dataset names, and live DB/mount status; "
    "(2) search_phages — filter by accession/accessions, dataset/datasets, completeness, lifestyle, taxonomy, "
    "host or structured host_rank+host_name (Phylum..Species), cluster, subcluster, is_rep, length_min/max, gc_min/max "
    "(pass save_tsv=true to export up to 2,000 matching rows to results/phagescope/ as TSV); "
    "(3) get_phage — full metadata, 6-rank host lineage, lifestyle, and protein_count for one phage_id or accession; "
    "(4) aggregate — fast SQL GROUP BY distribution (dataset, completeness, lifestyle, host_genus, host_species, "
    "host_family, host_phylum, taxonomy, cluster) over any filtered phage cohort; "
    "(5) search_proteins — search proteins on a phage (phage_id/accession + product_query/function_class), fetch "
    "protein_ids in batch, or search the 12.6M protein families (family_query, dominant_class, min_members, min_datasets); "
    "(6) family_members — inspect members of a protein family_id (optional subfamily_id, dataset, include_sequence); "
    "(7) features — query trna, crispr, anticrispr, terminator, virulence (VFDB), amr (CARD), or transmembrane (TMHMM) "
    "by phage_id/accession or cross-dataset keyword; "
    "(8) structures — check 3D structure availability/pLDDT for up to 50 protein_ids (set materialize_pdb=true to copy .pdb files); "
    "(9) materialize — export UNTRUNCATED full-length genomes (.fasta), proteins (.faa across all 27 datasets), "
    "annotations (.gff3), cohort metadata (.tsv), or 3D structures (.pdb) directly into the session workspace "
    "(results/phagescope/<output_subdir>/) ready for bio_tools (CheckV, geNomad, PhaBOX2, Prodigal, HMMER, MMseqs2, "
    "FastANI, BLAST, SeqKit) and execute_code."
)

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(OPERATIONS),
            "description": "Operation to execute on PhageScope V2 catalog.",
        },
        "phage_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Numeric PhageScope catalog ID.",
        },
        "phage_ids": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "description": "Batch of numeric phage IDs (up to 500 for search, 200 for materialize).",
        },
        "accession": {
            "type": "string",
            "description": "Single phage accession ID (e.g. 'NC_001416.1').",
        },
        "accessions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Batch of phage accession IDs (up to 500 for search, 200 for materialize).",
        },
        "dataset": {
            "type": "string",
            "description": "Dataset source name (e.g. Genbank, RefSeq, GPD, GVD, MGV, IMG_VR, GOV2, MetaVR, TemPhD, CHVD).",
        },
        "datasets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Multiple dataset source names to include.",
        },
        "completeness": {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ],
            "description": "CheckV tier(s): Complete, High-quality, Medium-quality, Low-quality, Not-determined.",
        },
        "lifestyle": {
            "type": "string",
            "enum": ["virulent", "temperate"],
            "description": "Predicted phage lifestyle (applies to annotated genomes).",
        },
        "taxonomy": {
            "type": "string",
            "description": "Phage taxonomy filter (prefix by default; set taxonomy_match='contains' for substring).",
        },
        "taxonomy_match": {
            "type": "string",
            "enum": ["prefix", "contains"],
            "description": "Match mode for taxonomy filter.",
        },
        "host": {
            "type": "string",
            "description": "Host text filter on phages.host (prefix by default; set host_match='contains' for substring).",
        },
        "host_match": {
            "type": "string",
            "enum": ["prefix", "contains"],
            "description": "Match mode for host text filter.",
        },
        "host_rank": {
            "type": "string",
            "enum": ["Phylum", "Class", "Order", "Family", "Genus", "Species"],
            "description": "Structured host taxonomic rank in phage_hosts (use with host_name).",
        },
        "host_name": {
            "type": "string",
            "description": "Exact taxonomic name at host_rank (e.g. host_rank='Genus', host_name='Pseudomonas').",
        },
        "cluster": {
            "type": "string",
            "description": "Genome cluster prefix (e.g. 'Cluster_123').",
        },
        "subcluster": {
            "type": "string",
            "description": "Genome subcluster prefix (e.g. 'Subcluster_456').",
        },
        "is_rep": {
            "type": "boolean",
            "description": "Filter non-redundant representative genomes (true/false).",
        },
        "is_mapped": {
            "type": "boolean",
            "description": "false = annotated genomes (1.35M), true = low-quality mapped records (1.22M).",
        },
        "length_min": {"type": "integer", "minimum": 0, "description": "Minimum genome length (bp)."},
        "length_max": {"type": "integer", "minimum": 1, "description": "Maximum genome length (bp)."},
        "gc_min": {"type": "number", "description": "Minimum GC content (%)."},
        "gc_max": {"type": "number", "description": "Maximum GC content (%)."},
        "sort_by": {
            "type": "string",
            "enum": ["id", "length_desc", "length_asc"],
            "description": "Sort order for search_phages / materialize.",
        },
        "include_total": {
            "type": "boolean",
            "description": "Run exact COUNT(*) in search_phages (default false for fast pagination).",
        },
        "group_by": {
            "type": "string",
            "enum": [
                "dataset",
                "completeness",
                "lifestyle",
                "taxonomy",
                "cluster",
                "host_phylum",
                "host_class",
                "host_order",
                "host_family",
                "host_genus",
                "host_species",
            ],
            "description": "Dimension for operation='aggregate'.",
        },
        "protein_id": {"type": "string", "description": "Single protein ID."},
        "protein_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Batch of protein IDs (up to 500 for search/materialize, 50 for structures).",
        },
        "product_query": {
            "type": "string",
            "description": "Substring search on Protein_product within a phage's proteins (e.g. 'endolysin', 'tail').",
        },
        "function_class": {
            "type": "string",
            "description": "Substring filter on Protein_function_classification or dominant_class.",
        },
        "family_query": {
            "type": "string",
            "description": "Keyword search across 12.6M protein families' consensus_product (e.g. 'endolysin', 'holin', 'integrase').",
        },
        "family_id": {"type": "string", "description": "Protein family ID (e.g. for family_members or materialize)."},
        "subfamily_id": {"type": "string", "description": "Optional protein subfamily ID."},
        "min_members": {"type": "integer", "minimum": 1, "description": "Minimum family member_count."},
        "min_datasets": {"type": "integer", "minimum": 1, "description": "Minimum distinct datasets represented in a family."},
        "include_sequence": {
            "type": "boolean",
            "description": "Include amino-acid sequence preview (up to 2,000 aa) in search_proteins / family_members.",
        },
        "feature_type": {
            "type": "string",
            "enum": ["trna", "crispr", "anticrispr", "terminator", "virulence", "amr", "transmembrane"],
            "description": "Functional annotation track for operation='features'.",
        },
        "keyword": {
            "type": "string",
            "description": "Keyword filter for features (e.g. CARD gene in 'amr', VFDB hit in 'virulence', tRNA type in 'trna').",
        },
        "kinds": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["fasta", "faa", "gff3", "metadata_tsv", "pdb"],
            },
            "description": "File types to materialize into session workspace (default ['fasta', 'metadata_tsv'] for phages, ['faa'] for proteins).",
        },
        "combine": {
            "type": "boolean",
            "description": "Write combined genomes.fasta / proteins.faa for multi-phage materialization (default true).",
        },
        "per_phage_files": {
            "type": "boolean",
            "description": "Also write individual fasta/<acc>.fasta and faa/<acc>.faa files when combine=true.",
        },
        "materialize_pdb": {
            "type": "boolean",
            "description": "Copy available .pdb structure files into session workspace during structures/materialize.",
        },
        "save_tsv": {
            "type": "boolean",
            "description": "Export search_phages results (up to max_items, max 2,000) to a session TSV file.",
        },
        "output_subdir": {
            "type": "string",
            "description": "Optional subdirectory name under results/phagescope/ for materialized files.",
        },
        "max_items": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2000,
            "description": "Maximum records to export/materialize (default 50 for materialize, 500 for save_tsv).",
        },
        "page": {"type": "integer", "minimum": 1, "description": "1-based page number."},
        "pagesize": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Page size (default 25, max 500)."},
        "timeout": {"type": "number", "description": "Timeout in seconds (default 45, max 120)."},
    },
    "required": ["operation"],
}
