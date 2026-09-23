"""Native (DeepThink function-calling) tool schema content — single home.

Each entry carries the exact ``description`` and ``parameters`` that the
native LLM path presents for a tool, plus a one-line note on how it relates
to the tool's base ``tools_impl`` definition. The OpenAI function envelope
(``{"type": "function", "function": {...}}``) is generated mechanically by
``app/services/tool_schemas.py`` — never hand-write envelopes here.

``bio_tools`` keeps only its static shell here: the ``tool_name`` enum and
the description are built dynamically from
``tool_box/bio_tools/tools_config.json`` by the generator (that dynamic
behaviour is intentional and preserved). ``verify_task`` has no tools_impl
base dict and stays next to the generator (app layer) by design.
"""

from __future__ import annotations

from typing import Any, Dict

NATIVE_TOOL_CONTENT: Dict[str, Dict[str, Any]] = {
    "web_search": {
        # 与 impl 双向漂移：native 多 queries、缺 max_results/provider —— 历史漂移，保留现状
        "description": (
            "Broad web search via Alibaba DashScope Responses API (built-in web_search tool). "
            "Use for web-based queries only, NOT for local files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "queries": {
                    "type": "array",
                    "description": "Optional focused subqueries for parallel search on broad comparison tasks.",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 6,
                },
            },
            "required": ["query"],
        },
    },
    "sequence_fetch": {
        # 参数键集与 impl 一致，仅字段文案差异 —— 历史漂移，保留现状
        "description": (
            "Deterministic accession-to-FASTA downloader with strict domain allowlist. "
            "Use this when the user asks to download FASTA by accession IDs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {
                    "type": "string",
                    "description": "Single accession ID (mutually exclusive with accessions).",
                },
                "accessions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple accession IDs (mutually exclusive with accession).",
                },
                "database": {
                    "type": "string",
                    "enum": ["nuccore", "protein"],
                    "description": "NCBI database type.",
                },
                "format": {
                    "type": "string",
                    "enum": ["fasta"],
                    "description": "Output format (FASTA only).",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
                "output_name": {
                    "type": "string",
                    "description": "Optional output filename.",
                },
                "timeout_sec": {
                    "type": "number",
                    "description": "Network timeout in seconds.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum response payload size in bytes.",
                },
            },
            "anyOf": [
                {"required": ["accession"]},
                {"required": ["accessions"]},
            ],
        },
    },
    "url_fetch": {
        # 参数键集与 impl 一致，仅字段文案差异 —— 历史漂移，保留现状
        "description": (
            "Download a file from a public http/https URL into the current task/session output directory. "
            "Use this for direct public link downloads instead of code_executor."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public http/https URL to download.",
                },
                "output_name": {
                    "type": "string",
                    "description": "Optional output filename.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
                "timeout_sec": {
                    "type": "number",
                    "description": "Network timeout in seconds.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum response size in bytes.",
                },
                "allowed_content_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional allowed MIME types (for example application/pdf or text/*).",
                },
                "sha256": {
                    "type": "string",
                    "description": "Optional expected sha256 hex digest.",
                },
            },
            "required": ["url"],
        },
    },
    "file_operations": {
        # 参数键集与 impl 一致；native 描述显著更长（补充任务内相对路径等指引）—— 有意调优（指引文案），保留现状
        "description": "File system operations: list directories, read/write files, profile/census directories, copy/move/delete. Use profile/census for compact directory-wide evidence before making all/every/completed claims. Use write when the user asks to save, export, create, or update a Markdown/text/JSON/CSV artifact instead of pasting content in chat. In task execution, prefer relative output paths so writes land in the current task output directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list", "read", "write", "copy", "move", "delete", "exists", "info", "profile", "census"],
                    "description": "The file operation to perform. profile/census are read-only directory evidence operations.",
                },
                "path": {
                    "type": "string",
                    "description": "Target file or directory path. Prefer relative paths or bare filenames for task outputs; avoid absolute session workspace paths for final deliverables.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (only for write operation). Required when creating requested saved reports, summaries, or text artifacts.",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination path (only for copy/move operations). Prefer task-relative destinations for final outputs.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional file matching pattern for list/profile/census operations.",
                },
            },
            "required": ["operation", "path"],
        },
    },
    "code_executor": {
        # native 有意收窄：impl 另有 allowed_tools/add_dirs，描述也不提 Claude Code —— 有意调优（参数面收窄）
        "description": (
            "Execute Python code for data analysis, visualization, or computation. "
            "Errors are returned transparently with fix guidance — you can inspect "
            "the generated code and error, then retry with a revised task description. "
            "For standard bioinformatics tasks, prefer bio_tools first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Description of the code task to execute.",
                },
            },
            "required": ["task"],
        },
    },
    "graph_rag": {
        # native 暴露 mode(global/local/hybrid)，impl 为 hops/top_k/focus_entities/return_subgraph —— 历史漂移，保留现状
        "description": "Query a knowledge graph for structured information retrieval.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The knowledge query.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["global", "local", "hybrid"],
                    "description": "Search mode. Default: hybrid.",
                },
            },
            "required": ["query"],
        },
    },
    "document_reader": {
        # native 有意收窄：operation 枚举不含 read_image，且不暴露 use_ocr —— 有意调优（参数面收窄）
        "description": (
            "Read local documents with format-aware parsing (.docx, .pdf, .txt, .md). "
            "For .csv and .tsv, returns an automatic preview (first ~150 lines) as text; "
            "for full table stats, filtering, or plots use code_executor. "
            "Do not use for .xlsx/.json/.parquet (use code_executor)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["read_any", "read_pdf", "read_text"],
                    "description": "Reading operation type.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the document file.",
                },
            },
            "required": ["operation", "file_path"],
        },
    },
    "vision_reader": {
        # native 有意收窄：仅 operation/file_path，impl 另有 image_path/page_number(s)/region/question/language/max_pages —— 有意调优（参数面收窄）
        "description": "Read PDFs and images using a vision model. For visual OCR, figures, and equations only.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["read_pdf", "read_image", "ocr_page"],
                    "description": "Vision operation type.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the image or PDF file.",
                },
            },
            "required": ["operation", "file_path"],
        },
    },
    "bio_tools": {
        # 动态条目：description 与 tool_name 枚举由生成器从 tools_config.json 构建（有意保留动态行为）；
        # native 参数面较 impl 少 session_id —— 有意调优（参数面收窄）
        "description": None,
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "The bioinformatics tool to run.",
                },
                "operation": {
                    "type": "string",
                    "description": (
                        "Operation to perform (e.g., stats, grep, predict, help). "
                        "Use 'job_status' to query a submitted background job."
                    ),
                },
                "input_file": {
                    "type": "string",
                    "description": "Absolute path to the input file.",
                },
                "sequence_text": {
                    "type": "string",
                    "description": (
                        "Inline FASTA or raw sequence text. Use this when no input file is "
                        "available. Mutually exclusive with input_file."
                    ),
                },
                "output_file": {
                    "type": "string",
                    "description": "Output filename or directory fragment.",
                },
                "params": {
                    "type": "object",
                    "description": "Additional tool-specific parameters.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Execution timeout in seconds. <=0 disables execution timeout "
                        "(for long-running tools)."
                    ),
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "If true, submit bio_tools execution as a background job and "
                        "return immediately with job_id. Recommended only for long-running "
                        "operations that do not need immediate in-turn output."
                    ),
                },
                "job_id": {
                    "type": "string",
                    "description": "Background job id used with operation='job_status'.",
                },
            },
            "required": ["tool_name", "operation"],
        },
    },
    "literature_pipeline": {
        # native 有意收窄：impl 另有 include_europepmc/include_biorxiv/biorxiv_years_back —— 有意调优（参数面收窄）
        "description": (
            "Collect a literature evidence pack from PubMed/PMC. "
            "Produces evidence.md, references.bib, and library.jsonl for downstream drafting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "PubMed search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of PubMed records to collect.",
                },
                "out_dir": {
                    "type": "string",
                    "description": "Optional project-relative output directory.",
                },
                "download_pdfs": {
                    "type": "boolean",
                    "description": "Whether to try downloading PMC PDFs.",
                },
                "max_pdfs": {
                    "type": "integer",
                    "description": "Maximum number of PDFs to download when download_pdfs=true.",
                },
                "user_agent": {
                    "type": "string",
                    "description": "Optional HTTP user-agent override.",
                },
                "proxy": {
                    "type": "string",
                    "description": "Optional HTTP proxy URL.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
            },
            "required": ["query"],
        },
    },
    "review_pack_writer": {
        # native 有意收窄：impl 另有 generation/evaluation/merge model+provider、out_dir、proxy、user_agent —— 有意调优（参数面收窄）
        "description": (
            "Generate a literature-backed review draft by first collecting evidence "
            "with literature_pipeline and then drafting with manuscript_writer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "High-level review topic.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional explicit PubMed query override.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of PubMed records to collect.",
                },
                "download_pdfs": {
                    "type": "boolean",
                    "description": "Whether to try downloading PMC PDFs.",
                },
                "max_pdfs": {
                    "type": "integer",
                    "description": "Maximum number of PDFs to download when download_pdfs=true.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional project-relative output path for the review draft.",
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of manuscript sections to draft.",
                },
                "max_revisions": {
                    "type": "integer",
                    "description": "Maximum revision rounds per section.",
                },
                "evaluation_threshold": {
                    "type": "number",
                    "description": "Section evaluation pass threshold from 0 to 1.",
                },
                "keep_workspace": {
                    "type": "boolean",
                    "description": "Whether to keep intermediate drafting workspace artifacts.",
                },
                "task": {
                    "type": "string",
                    "description": "Optional direct manuscript task override.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
            },
            "required": ["topic"],
        },
    },
    "phagescope": {
        # native 有意收窄 + 强化指引：impl 34 个参数键，native 15 个；描述长 4 倍（workflow 指引）—— 有意调优，保留现状
        "description": (
            "PhageScope cloud platform for phage genome analysis. ASYNC service. "
            "Use for reachability and remote workflow questions: call action=ping first (HTTP check); "
            "do not infer connectivity from local file listing. "
            "Documented flows center on userid; do not ask users for a generic 'API Token' unless they "
            "or platform docs explicitly require Bearer auth. "
            "Workflow: submit -> task_list/task_detail -> result. "
            "After submit, report the taskid and tell the user to check status later. "
            "IMPORTANT: result/quality/task_detail/download return API/JSON (or one file), not a full local folder tree; "
            "for a complete on-disk bundle (metadata/, annotation/, raw_api_responses/, summary.json), use action=save_all "
            "with the numeric taskid after the remote task succeeds. "
            "Batch: action=batch_submit submits multiple phage ids (default strategy multi_one_task = one remote taskid) and "
            "writes a manifest JSON under the session work directory; batch_reconcile diffs requested ids vs result/phage rows; "
            "batch_retry re-submits missing ids one strain per task. Prefer these over memorizing taskids in chat."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "ping",
                        "input_check",
                        "submit",
                        "cluster_submit",
                        "task_list",
                        "task_detail",
                        "task_log",
                        "result",
                        "quality",
                        "download",
                        "query",
                        "save_all",
                        "batch_submit",
                        "batch_reconcile",
                        "batch_retry",
                        "bulk_download",
                    ],
                    "description": (
                        "PhageScope action. Use ping for connectivity/API checks (no other params required). "
                        "Use task_list with userid to verify account-scoped access when ping succeeds. "
                        "Use batch_submit/batch_reconcile/batch_retry for multi-strain workflows with manifest persistence. "
                        "Use bulk_download to download public datasets from the PhageScope download page; "
                        "pass data type names via modulelist (valid: phage_meta_data, annotated_protein, "
                        "transcription_terminator, trna_tmrna, anticrispr_protein, crispr_array, "
                        "antimicrobial_resistance_gene, virulent_factor, transmembrane_protein, "
                        "phage_fasta, protein_fasta, gff3) and datasource names via phage_ids "
                        "(valid: refseq, genbank, embl, ddbj, phagesdb, gvd, gpd, mgv, temphd, "
                        "chvd, igvd, img_vr, gov2, stv). Omit both for all."
                    ),
                },
                "base_url": {
                    "type": "string",
                    "description": "Optional API base URL override (defaults from server configuration).",
                },
                "token": {
                    "type": "string",
                    "description": (
                        "Rarely needed. Optional Bearer token only if the platform/account explicitly "
                        "requires it; omit by default (phageapi-style flows use userid, not a mandatory token)."
                    ),
                },
                "userid": {"type": "string", "description": "User ID for the PhageScope platform."},
                "phageid": {"type": "string", "description": "Single phage ID or accession."},
                "phageids": {"type": "string", "description": "Comma-separated phage IDs."},
                "taskid": {
                    "type": "string",
                    "description": (
                        "Numeric PhageScope remote task ID for status/result queries "
                        "(e.g., 37468), not local job ids like act_xxx. "
                        "Use this field name; if you emit task_id instead, the server maps it to taskid."
                    ),
                },
                "modulelist": {
                    "type": "string",
                    "description": (
                        "Comma-separated module names. For submit/batch_submit (Annotation Pipeline), "
                        "use submit modules only: "
                        "quality, annotation, host, lifestyle, terminator, taxonomic, "
                        "trna, anticrispr, crispr, arvf, transmembrane. Do not use result/output names "
                        "such as proteins, phage_detail, phagefasta, or tree in submit modulelist. "
                        "For action=batch_submit, if omitted the backend defaults to [quality]. "
                        "For action=bulk_download, use dataset data-type names instead: "
                        "phage_meta_data, annotated_protein, transcription_terminator, trna_tmrna, "
                        "anticrispr_protein, crispr_array, antimicrobial_resistance_gene, "
                        "virulent_factor, transmembrane_protein, phage_fasta, protein_fasta, gff3. "
                        "Omit for all data types."
                    ),
                },
                "result_kind": {
                    "type": "string",
                    "enum": ["quality", "proteins", "phage_detail", "modules", "tree", "phagefasta"],
                    "description": "Type of result to retrieve.",
                },
                "phage_ids": {
                    "description": (
                        "For batch_submit: array of phage accessions or a single string with semicolons/newlines. "
                        "For bulk_download: datasource names to download (e.g. 'refseq', 'genbank;embl'). "
                        "Valid datasources: refseq, genbank, embl, ddbj, phagesdb, gvd, gpd, mgv, "
                        "temphd, chvd, igvd, img_vr, gov2, stv. Omit for all datasources."
                    ),
                },
                "batch_id": {
                    "type": "string",
                    "description": "Batch manifest id (batch_reconcile, batch_retry; optional on batch_submit to fix the id).",
                },
                "strategy": {
                    "type": "string",
                    "description": "batch_submit: multi_one_task (default) or per_strain.",
                },
                "manifest_path": {"type": "string", "description": "Optional explicit manifest JSON path."},
                "retry_phage_ids": {
                    "description": "batch_retry: explicit list of ids; if omitted, uses last reconcile missing list.",
                },
                "phage_ids_file": {
                    "type": "string",
                    "description": "batch_submit: optional path to newline-separated phage ids.",
                },
            },
            "required": ["action"],
        },
    },
    "phagescope_research": {
        # native 有意收窄：impl 另有 max_rows/top_n；action 枚举 native 少 deep_profile —— 有意调优（参数面收窄）
        "description": (
            "Prepare and audit the local PhageScope public dataset for host taxon "
            "prediction research. Use this before code_executor for PhageScope ML tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["audit", "research_plan", "prepare_metadata_table"],
                    "default": "audit",
                    "description": "Operation to perform.",
                },
                "data_dir": {
                    "type": "string",
                    "description": "Local PhageScope data directory. Defaults to PHAGESCOPE_DATA_DIR or <repo>/phagescope.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory for prepared TSV/JSON outputs.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional chat session id for session-scoped outputs.",
                },
                "label_level": {
                    "type": "string",
                    "enum": ["raw", "genus", "species_like"],
                    "default": "genus",
                    "description": "How to standardize Host labels for model targets.",
                },
                "min_label_count": {
                    "type": "integer",
                    "default": 20,
                    "description": "Minimum class count retained in prepared metadata table.",
                },
                "completeness": {
                    "description": "Allowed Completeness values. Defaults to High-quality and Medium-quality.",
                },
                "split_group": {
                    "type": "string",
                    "enum": ["subcluster", "cluster", "phage_id"],
                    "default": "subcluster",
                    "description": "Grouping key for deterministic leakage-aware train/val/test split.",
                },
            },
        },
    },
    "result_interpreter": {
        # native 有意收窄：impl 另有 data_dir/data_paths/max_depth/node_budget/work_dir —— 有意调优（参数面收窄）
        "description": (
            "Data analysis and result interpretation tool. Supports lightweight metadata/profile "
            "inspection plus code-backed analysis for CSV, TSV, MAT, NPY, H5AD, and TXT helper files. "
            "Use profile/metadata to inspect existing plan or task outputs before synthesizing reports from large tables."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["metadata", "profile", "generate", "execute", "analyze", "plan_analyze"],
                    "description": (
                        "Analysis operation. 'profile' is the deterministic dataset-overview path; "
                        "'analyze' is the full pipeline; 'metadata' is a schema peek; "
                        "'plan_analyze' is for plan-linked workflows."
                    ),
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute paths to data files.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Single file path (for metadata/profile operations).",
                },
                "task_title": {
                    "type": "string",
                    "description": (
                        "Short title for analyze/generate/plan_analyze. "
                        "If omitted, the server uses a sensible default."
                    ),
                },
                "task_description": {
                    "type": "string",
                    "description": (
                        "What to compute or interpret. Required for quality results; "
                        "if omitted, a generic default is used."
                    ),
                },
                "code": {
                    "type": "string",
                    "description": "Python code (for execute operation).",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Optional output directory (plan_analyze).",
                },
            },
            "required": ["operation"],
        },
    },
    "scientific_figure_generator": {
        # 参数键集与 impl 一致；native 描述显著更长（强指引）—— 有意调优（指引文案），保留现状
        "description": (
            "Preferred tool for scientific composite figures and publication-style plots. "
            "Use this when the user asks for a figure, plot, chart, visualization, PNG/PDF output, "
            "English legend/summary, provenance TSV, QA JSON, or Deliverables publication. "
            "It accepts inline tabular rows or CSV/TSV/JSON/JSONL paths and generates PNG, optional PDF, "
            "summary.md, provenance TSV, QA JSON, and deliverable_submit artifacts. "
            "Prefer this over code_executor for standard scientific figure generation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "English figure title.",
                },
                "datasets": {
                    "type": "array",
                    "description": "Datasets as inline rows or file paths.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Dataset name referenced by panels."},
                            "path": {"type": "string", "description": "CSV/TSV/JSON/JSONL file path."},
                            "format": {"type": "string", "description": "Optional format override: csv, tsv, json, jsonl."},
                            "rows": {
                                "type": "array",
                                "description": "Inline tabular rows.",
                                "items": {"type": "object"},
                            },
                        },
                    },
                },
                "panels": {
                    "type": "array",
                    "description": (
                        "Panel specs. Supported type values: auto, bar, line, scatter, heatmap, table. "
                        "Common fields: dataset, type, title, x, y, value, row, column, top_n."
                    ),
                    "items": {"type": "object"},
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory. Defaults to the task/session work directory.",
                },
                "output_basename": {
                    "type": "string",
                    "description": "Base filename for PNG/PDF/QA/provenance artifacts.",
                },
                "formats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Output formats. PNG is always written; include pdf for PDF.",
                },
                "dpi": {"type": "integer", "description": "PNG DPI, default 300."},
                "publish": {
                    "type": "boolean",
                    "description": "Whether to publish generated artifacts to session Deliverables.",
                    "default": True,
                },
            },
            "required": ["datasets"],
        },
    },
    "manuscript_writer": {
        # native 有意收窄：impl 另有 article_mode 及 generation/evaluation/merge model+provider —— 有意调优（参数面收窄）
        "description": (
            "Write a research manuscript, evidence-based report, structured summary, or section with citation-aware drafting, "
            "evaluation, and merge support. Use this when the user asks to generate/save a research report or Markdown summary from evidence files. "
            "Author manuscript content as Markdown first; use .md outputs as the source of truth and render PDF only in a later conversion step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Writing task or manuscript instruction.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Project-relative output file path. Prefer .md for manuscripts; do not use .pdf for draft_only outputs.",
                },
                "context_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional supporting context files such as evidence.md, manifest files, CSV/TSV/JSON result tables, or references.bib.",
                },
                "analysis_path": {
                    "type": "string",
                    "description": "Optional project-relative analysis memo path.",
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of manuscript sections to draft.",
                },
                "max_revisions": {
                    "type": "integer",
                    "description": "Maximum revision rounds per section.",
                },
                "evaluation_threshold": {
                    "type": "number",
                    "description": "Section evaluation pass threshold from 0 to 1.",
                },
                "max_context_bytes": {
                    "type": "integer",
                    "description": "Maximum context size loaded from supporting files.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
                "keep_workspace": {
                    "type": "boolean",
                    "description": "Whether to keep intermediate drafting workspace artifacts.",
                },
                "draft_only": {
                    "type": "boolean",
                    "description": "Assemble a lightweight local draft without the full staged evaluation and polish pipeline.",
                },
            },
            "required": ["task", "output_path"],
        },
    },
    "deliverable_submit": {
        # 参数键集与 impl 一致，仅描述文案差异（impl 更长）—— 历史漂移，保留现状
        "description": (
            "Promote specific files into the session Deliverables tree for paper, report, summary, figure, table, or submission use. "
            "Use after files already exist and the user wants them published into Deliverables or made visible as final outputs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "publish": {
                    "type": "boolean",
                    "description": "If false, do not copy files in this call.",
                },
                "artifacts": {
                    "type": "array",
                    "description": "Artifacts to copy into deliverables/latest/<module>/.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Absolute or project-relative source file path.",
                            },
                            "module": {
                                "type": "string",
                                "enum": ["code", "image_tabular", "paper", "refs", "docs"],
                                "description": "Target Deliverables module.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Optional audit note for why this artifact is being published.",
                            },
                        },
                        "required": ["path", "module"],
                    },
                },
            },
            "required": ["artifacts"],
        },
    },
    "plan_operation": {
        # native 有意收窄：impl 另有 expand_composites/new_status/note/target_task_id；impl 描述更长 —— 有意调优（参数面收窄）
        "description": (
            "Plan creation, optimization, and execution tool. "
            "Operations: create, bind, review, optimize, get, execute_all, todo_list. "
            "CRITICAL: When the user wants to execute the entire plan or all tasks, "
            "you MUST use operation='execute_all'. Do NOT execute tasks one by one. "
            "execute_all launches a background job that handles all tasks automatically. "
            "Use create for new structured plans, then prefer review to persist rubric metadata. "
            "Use bind to switch the current session to a different plan (e.g. '切换到 plan X', '绑定 plan X'). "
            "Use get/review/optimize for bound plans. "
            "Research with web_search first only when latest external evidence materially affects the plan. "
            "For optimize, use concrete change objects such as add_task, update_task, update_description, "
            "delete_task, or reorder_task; keep update_task fields at the top level. If changes are omitted, optimize may auto-generate them from the latest rubric review. "
            "Do not use this tool to mark the currently executing task completed or failed; current-task status is auto-synced from tool execution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["create", "bind", "review", "optimize", "get", "execute_all", "todo_list", "update_task"],
                    "description": "Plan operation to perform. Use execute_all to run all pending tasks in background. Use bind to switch to a different plan.",
                },
                "title": {"type": "string", "description": "Plan title (for create)."},
                "description": {"type": "string", "description": "Plan goal description (for create)."},
                "plan_id": {"type": "integer", "description": "Plan ID (for review/optimize/get)."},
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "instruction": {"type": "string"},
                            "dependencies": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["name", "instruction"],
                    },
                    "description": "Task list (for create).",
                },
                "changes": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optimization changes (for optimize).",
                },
            },
            "required": ["operation"],
        },
    },
    "terminal_session": {
        # native 有意收窄：impl 另有 ssh_config/cols/rows/approval_id/approved/limit 外的多项（start_ts/end_ts/event_type）—— 有意调优（参数面收窄）
        "description": (
            "Manage interactive terminal sessions (sandbox PTY or remote SSH). "
            "Use 'create' or 'ensure' to get a terminal tied to the current chat session or execution context, "
            "'write' to send commands (plain text or base64), "
            "'list' to see active sessions, "
            "'close' to terminate a session, "
            "'replay' / 'audit' for history. "
            "Prefer sandbox mode for local scripts/debugging; use ssh mode to reach "
            "remote servers (e.g. bio-tools GPU node). "
            "For 'write', terminal_id is auto-resolved if omitted — no need to call 'ensure' first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "create", "ensure", "list", "close",
                        "write", "resize",
                        "approve", "reject", "pending_approvals",
                        "replay", "audit",
                    ],
                    "description": "Operation to perform on the terminal session.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Chat session ID to associate the terminal with.",
                },
                "terminal_id": {
                    "type": "string",
                    "description": "Terminal instance UUID (required for write/close/replay/audit).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["sandbox", "ssh"],
                    "description": "Terminal backend: 'sandbox' for local PTY, 'ssh' for remote.",
                },
                "data": {
                    "type": "string",
                    "description": "Command text to send (for write operation).",
                },
                "encoding": {
                    "type": "string",
                    "enum": ["utf-8", "base64"],
                    "description": "Encoding of the data field (default utf-8).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return for audit/replay (default 500).",
                },
            },
            "required": ["operation"],
        },
    },
}
