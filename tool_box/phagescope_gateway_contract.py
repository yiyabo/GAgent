"""Contract from the supplied PhageScope Agent Gateway (2026-09-11)."""
from copy import deepcopy

MODULES = (
    "annotation", "quality", "host", "host_cherry", "lifestyle", "lifestyle_phatyp",
    "viralverify", "trna", "crispr", "taxonomic_phagcn", "taxonomic", "alignment",
    "anticrispr", "arvf", "transmembrane", "terminator", "tree", "cluster",
)
NEEDS_ANNOTATION = {"taxonomic", "alignment", "anticrispr", "arvf", "transmembrane", "terminator", "cluster"}
PHAGE_FILTERS = {
    "accession", "dataset", "completeness", "lifestyle", "taxonomy", "host", "cluster",
    "length_min", "length_max", "gc_min", "gc_max",
}
PAGE = {"page", "pagesize"}
# operation -> channel, path, URL identifiers, accepted query parameters
ROUTES = {
    "data_health": ("data", "health/", (), set()),
    "analysis_health": ("analysis", "health/", (), set()),
    "overview": ("data", "stats/overview/", (), set()),
    "datasets": ("data", "datasets/", (), set()),
    "phages": ("data", "phages/", (), PHAGE_FILTERS | PAGE),
    "phage": ("data", "phages/{phage_id}/", ("phage_id",), set()),
    "phage_proteins": ("data", "phages/{phage_id}/proteins/", ("phage_id",), PAGE | {"include_sequence"}),
    "phage_features": ("data", "phages/{phage_id}/features/", ("phage_id",), PAGE | {"type"}),
    "phage_sequence": ("data", "phages/{phage_id}/sequence/", ("phage_id",), {"max_bases"}),
    "protein": ("data", "proteins/{protein_id}/", ("protein_id",), {"include_sequence"}),
    "hosts": ("data", "hosts/", (), PAGE | {"rank", "name"}),
    "families": ("data", "families/", (), {"protein_id"}),
    "family": ("data", "families/{family_id}/", ("family_id",), set()),
    "family_members": ("data", "families/{family_id}/members/", ("family_id",), PAGE | {"dataset"}),
    "structures": ("data", "structures/available/", (), {"protein_id", "protein_ids"}),
    "status": ("analysis", "jobs/{job_id}/", ("job_id",), set()),
    "results": ("analysis", "jobs/{job_id}/results/", ("job_id",), {"module"}),
}
OPERATIONS = ("capabilities", *ROUTES, "submit", "save_results")
GUIDANCE = (
    "Agent Gateway: action=gateway, gateway_action=capabilities discovers configured channels. "
    "gateway_action supports data_health, analysis_health, overview, datasets, phages, phage, "
    "phage_proteins, phage_features, phage_sequence, protein, hosts, families, family, family_members, "
    "structures, submit, status, results, save_results. Put inputs in gateway_params. "
    "Use phage_id for the numeric catalog id; use job_id for an analysis job. "
    "submit accepts modules and exactly one of fasta, file_path, accession; returns a job_id promptly. "
    "Then query status and save_results into the current task workspace. API keys come from server "
    "configuration. Results may be truncated previews: inspect truncation metadata before using them "
    "as full input. No content approval is required. Legacy website actions remain available. "
)


def gateway_schema():
    properties = {
        "phage_id": {"type": "integer", "minimum": 1},
        "job_id": {"type": "integer", "minimum": 1},
        "protein_id": {"type": "string"},
        "protein_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
        "family_id": {"type": "string"},
        "page": {"type": "integer", "minimum": 1},
        "pagesize": {"type": "integer", "minimum": 1, "maximum": 50},
        "include_sequence": {"type": "boolean"},
        "max_bases": {"type": "integer", "minimum": 1, "maximum": 200000},
        "type": {"type": "string", "enum": ["trna", "crispr", "anticrispr", "terminator", "virulence", "amr", "transmembrane"]},
        "rank": {"type": "string", "enum": ["Phylum", "Class", "Order", "Family", "Genus", "Species"]},
        "name": {"type": "string"},
        "modules": {"type": "array", "items": {"type": "string", "enum": list(MODULES)}, "minItems": 1, "maxItems": 12},
        "module": {"type": "string", "enum": list(MODULES)},
        "fasta": {"type": "string", "description": "Nucleotide FASTA text for submit; do not send a filename here."},
        "file_path": {"type": "string", "description": "Existing FASTA input file in the current session/task."},
        "save": {"type": "boolean", "description": "Save query response (and returned sequence) in the current task workspace."},
    }
    for key in PHAGE_FILTERS:
        properties[key] = {"type": "integer" if key.startswith("length_") else "number" if key.startswith("gc_") else "string"}
    return {
        "gateway_action": {"type": "string", "enum": list(OPERATIONS), "description": "Required with action=gateway. Use capabilities to discover all Agent Gateway operations."},
        "gateway_params": {"type": "object", "properties": properties, "additionalProperties": False,
                           "description": "Inputs for gateway_action. phages requires a filter; phage_* needs phage_id; hosts needs rank; families needs protein_id; results needs job_id+module; save_results needs job_id (modules optional)."},
    }


def extend_schema(content, parameters_key):
    """One gateway schema shared by the native and strict/code-mode surfaces."""
    content["description"] = GUIDANCE + content["description"]
    properties = content[parameters_key]["properties"]
    properties["action"]["enum"].append("gateway")
    properties.update(deepcopy(gateway_schema()))


def summarize_gateway_result(result):
    operation = result.get("gateway_action", "gateway")
    if not result.get("success"):
        return f"PhageScope gateway {operation}: {result.get('error') or result.get('error_code') or 'request failed'}"
    if operation == "capabilities":
        ready = [name for name, row in result.get("channels", {}).items() if row.get("ready")]
        return "PhageScope gateway configured channels: " + (", ".join(ready) or "none; connection configuration required")
    job = f" job #{result['job_id']}" if result.get("job_id") else ""
    state = f" ({result['execution_status']})" if result.get("execution_status") else ""
    files = result.get("produced_files") or []
    saved = f"; saved {len(files)} files" if files else ""
    preview = "; includes previews" if result.get("truncated_files") or result.get("text_only_files") else ""
    return f"PhageScope gateway {operation}{job}{state}{saved}{preview}"
