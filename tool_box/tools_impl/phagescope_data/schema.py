"""Schema definition for the phagescope_data tool."""

from __future__ import annotations

from typing import Any

OPERATIONS = (
    "overview",
    "datasets",
    "search_phages",
    "get_phage",
    "aggregate",
    "cohort_report",
    "search_proteins",
    "family_members",
    "features",
    "structures",
    "materialize",
)

DESCRIPTION = (
    "PREFERRED read-only PhageScope V2 catalog query, aggregation, and untruncated sequence/annotation/structure "
    "materializer (2.58M phages / 27 datasets, 131.5M proteins, 12.61M protein families, 7 feature tracks, ESMFold structures). "
    "Operations: "
    "(1) overview / datasets — catalog totals and live DB/mount status; "
    "(2) cohort_report — PREFERRED for cohort exploration: ONE call returns exact count, length stats, "
    "dataset/completeness/lifestyle/host-genus facets, and sample rows for any phage filter set "
    "(save_tsv=true also exports the cohort TSV); "
    "(3) search_phages — paged record listing by accession(s), dataset(s), completeness, lifestyle, taxonomy, "
    "host or host_rank+host_name, cluster, length/gc bounds (save_tsv=true exports up to 2,000 rows); "
    "(4) get_phage — full metadata + 6-rank host lineage + protein_count for one phage_id/accession; "
    "(5) aggregate — single-dimension GROUP BY distribution over any filtered cohort; "
    "(6) search_proteins — proteins on a phage (product_query/function_class), protein_ids in batch, or the "
    "12.6M protein families (family_query, min_members, min_datasets; family_search_mode='synonyms' "
    "expands a small curated alias list, 'literal' keeps SQL substring search); "
    "(7) family_members — members of one family_id (optional subfamily_id, dataset, include_sequence); "
    "(8) features — trna, crispr, anticrispr, terminator, virulence, amr, transmembrane by phage or cross-dataset keyword; "
    "(9) structures — ESMFold/AFDB availability/pLDDT for up to 50 protein_ids (materialize_pdb=true copies .pdb); "
    "(10) materialize — UNTRUNCATED genomes (.fasta), proteins (.faa), annotations (.gff3), metadata (.tsv), "
    "structures (.pdb) into results/phagescope/<output_subdir>/ ready for bio_tools and execute_code."
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
        "sample_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Representative sample rows returned by operation='cohort_report' (default 5).",
        },
        "group_by": {
            "anyOf": [
                {
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
                },
                {
                    "type": "array",
                    "items": {
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
                    },
                    "maxItems": 3,
                },
            ],
            "description": "Dimension(s) for operation='aggregate': a single dimension string, or up to 3 dimensions for a cross-tabulation (multi-dim requires the acceleration layer).",
        },
        "amr_free": {
            "type": "boolean",
            "description": "Only phages with zero annotated CARD antimicrobial-resistance hits in this snapshot (n_amr==0). Zero hits do not establish clinical safety; requires the acceleration layer.",
        },
        "virulence_free": {
            "type": "boolean",
            "description": "Only phages with zero annotated VFDB virulence-factor hits in this snapshot (n_virulence==0). Zero hits do not establish clinical safety; requires the acceleration layer.",
        },
        "min_trna": {
            "type": "integer",
            "minimum": 0,
            "description": "Minimum tRNA/tmRNA gene count (requires the acceleration layer).",
        },
        "has_crispr": {
            "type": "boolean",
            "description": "Only phages with at least one annotated CRISPR array. IMG_VR/MetaVR shared accessions with unknown CRISPR counts are excluded; requires the acceleration layer.",
        },
        "has_anticrispr": {
            "type": "boolean",
            "description": "Only phages carrying at least one anti-CRISPR protein (requires the acceleration layer).",
        },
        "require_files": {
            "type": "array",
            "items": {"type": "string", "enum": ["fasta", "faa", "gff3"]},
            "description": "Only phages whose FASTA/FAA/GFF3 files were present in the GAgent snapshot when built; actual availability is rechecked during materialization. Requires the acceleration layer.",
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
            "description": "Protein-family keyword: indexed full words/phrases in consensus_product or dominant_class when FTS5 is configured (e.g. 'endolysin', 'tail fibre').",
        },
        "family_search_mode": {
            "type": "string",
            "enum": ["auto", "literal", "fts", "synonyms"],
            "description": "Family keyword only: auto (default, FTS5 when configured, otherwise SQL); literal (original SQL substring); fts (indexed word/phrase); synonyms (FTS5 plus explicit curated aliases). FTS5/synonyms require a published family index; synonym hits are retrieval hints, not proof of function.",
        },
        "family_id": {"type": "string", "description": "Protein family ID (e.g. for family_members or materialize)."},
        "subfamily_id": {"type": "string", "description": "Optional protein subfamily ID."},
        "min_members": {"type": "integer", "minimum": 1, "description": "Minimum family member_count."},
        "min_datasets": {
            "type": "integer",
            "minimum": 1,
            "description": "Minimum distinct datasets represented in a family.",
        },
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
        "pagesize": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
            "description": "Page size (default 25, max 500).",
        },
        "timeout": {"type": "number", "description": "Timeout in seconds (default 45, max 120)."},
    },
    "required": ["operation"],
}
