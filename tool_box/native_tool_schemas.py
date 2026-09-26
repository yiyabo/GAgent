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
        # 已对齐 impl（2026-09-24 漂移合并）：补齐 provider/max_results，描述同步 impl
        "description": (
            "Broad web search via Alibaba DashScope Responses API using the built-in "
            "`web_search` tool (see Model Studio web-search docs). Default provider is `builtin` only. "
            "For broad comparison tasks, you can pass `queries` with 2-4 focused subqueries; they will run in parallel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query string to search for",
                },
                "queries": {
                    "type": "array",
                    "description": "Optional focused subqueries for parallel search on broad comparison tasks",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 6,
                },
                "provider": {
                    "type": "string",
                    "description": "Optional override: `builtin` (DashScope web_search), `perplexity`, or `tavily`",
                    "enum": ["builtin", "perplexity", "tavily"],
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (some providers may ignore this option)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
    },
    "sequence_fetch": {
        # 已对齐 impl（2026-09-24 漂移合并）：文案与默认值同步 impl
        "description": (
            "Deterministic accession-to-FASTA downloader with strict allowlist. "
            "Use this when users ask to download sequence FASTA by accession IDs."
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
                    "default": "nuccore",
                    "description": "NCBI database type.",
                },
                "format": {
                    "type": "string",
                    "enum": ["fasta"],
                    "default": "fasta",
                    "description": "Sequence output format.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional chat session id for session-scoped output storage.",
                },
                "output_name": {
                    "type": "string",
                    "description": "Optional output filename ('.fasta' appended if missing).",
                },
                "timeout_sec": {
                    "type": "number",
                    "default": 30.0,
                    "description": "Network timeout in seconds.",
                },
                "max_bytes": {
                    "type": "integer",
                    "default": 10485760,
                    "description": "Maximum allowed response bytes.",
                },
            },
            "anyOf": [
                {"required": ["accession"]},
                {"required": ["accessions"]},
            ],
        },
    },
    "url_fetch": {
        # 已对齐 impl（2026-09-24 漂移合并）：文案与默认值同步 impl，保留 native 路由指引句
        "description": (
            "Download a file from a public http/https URL into the current task/session output directory. "
            "Supports optional content-type and sha256 validation. "
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
                    "description": "Optional output file name. Must be a file name, not a path.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
                "timeout_sec": {
                    "type": "number",
                    "default": 60.0,
                    "description": "Network timeout in seconds.",
                },
                "max_bytes": {
                    "type": "integer",
                    "default": 52428800,
                    "description": "Maximum number of bytes to download.",
                },
                "allowed_content_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional allowed MIME types (exact match or major-type wildcard like text/*).",
                },
                "sha256": {
                    "type": "string",
                    "description": "Optional expected sha256 hex digest for integrity verification.",
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
        # 2026-09-24 描述修正：删除 "prefer bio_tools first"（本平台 bio_tools 永不上线，指引会把模型带向不可用工具）
        # 2026-09-24 降权：只读核验委派禁令——生产实证模型把只读审计反复派给本工具（单次 ~108s pi 开销干秒级的活）
        # 2026-09-26 对称化：补上 delegate_task 已有的完整排除清单 + 起步价。实测委派地板成本
        # 35-57s 墙钟 + 子 agent 3.2-3.5k token，哪怕任务只有一行；不写清排除项模型就会把
        # "数一下 CSV 行数" 也派出去（评测 t01 实证）。
        "description": (
            "Execute Python code for data analysis, visualization, or computation. This is a "
            "DELEGATION to the pi coding harness, not a local one-liner. Errors are returned "
            "transparently with fix guidance — you can inspect the generated code and error, "
            "then retry with a revised task description. "
            "Do NOT delegate: reading a single file; one-off counting, row totals, or "
            "statistics over data you already have; arithmetic; drawing a single plot; "
            "read-only checking, verification, auditing, or evidence extraction that does not "
            "modify files — ordinary tools (document_reader, file_operations, "
            "result_interpreter) or about five lines of execute_code answer those in seconds. "
            "A delegation pays a full agent run up front (30-60s) before any work starts, so "
            "reserve this tool for substantive implementation work."
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
        # 已对齐 impl（2026-09-24 漂移合并）：删除 impl 不支持的 mode（此前被静默丢弃、误导模型），
        # 暴露真实参数 top_k/hops/return_subgraph/focus_entities，描述同步 impl 的 LEGACY 警示
        "description": (
            "LEGACY small local triples graph (limited coverage). Prefer `lightrag_query` "
            "for literature/knowledge-graph questions. Use only if LightRAG is unavailable "
            "or the user explicitly requests the small local graph."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query statement, e.g., 'How do phages infect bacteria?'",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 12,
                    "description": "Number of most relevant triples to return (limited by system cap).",
                },
                "hops": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4,
                    "default": 1,
                    "description": "Number of hops to expand subgraph.",
                },
                "return_subgraph": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to return k-hop subgraph JSON.",
                },
                "focus_entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entity names to prioritize, can be used to reorder results.",
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
    "load_skill": {
        # 已对齐 impl（2026-09-24 新增）：渐进式披露第④步，模型按需拉取完整 SKILL.md
        "description": (
            "Load the complete SKILL.md of a named runtime skill on demand "
            "(progressive disclosure). Use when the available-skills summary names a "
            "skill relevant to the current task and you need its full instructions "
            "before proceeding. Unknown names return the available skill list. For "
            "large skills, pass 'section' to fetch one markdown section instead of "
            "the whole body."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact skill name (as listed in the available-skills summary).",
                },
                "section": {
                    "type": "string",
                    "description": (
                        "Optional markdown section filter (case-insensitive substring of a "
                        "heading). Use it to page through large skills whose body was truncated."
                    ),
                },
            },
            "required": ["name"],
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
        # native 有意收窄：impl 另有 max_rows/top_n —— 有意调优（参数面收窄）
        # 2026-09-24 漂移合并：action 枚举补 deep_profile（impl 主力动作，缺它 native 流走不通），
        # min_label_count 默认值 20→100 对齐 handler 真实默认
        "description": (
            "Prepare, audit, and deep-profile the local PhageScope public dataset for host taxon "
            "prediction research. Use deep_profile before code_executor or final synthesis for PhageScope ML/data exploration tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["audit", "deep_profile", "research_plan", "prepare_metadata_table"],
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
                    "default": 100,
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
        # native 有意收窄：impl 另有 generation/evaluation/merge model+provider —— 有意调优（参数面收窄）
        # 2026-09-24 漂移合并：补 article_mode（handler 支持，宣称的 review/synthesis 能力此前无法显式控制）；
        # max_revisions/evaluation_threshold/max_context_bytes 补 handler 真实默认值
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
                "article_mode": {
                    "type": "string",
                    "enum": ["auto", "review", "research"],
                    "description": "Optional article mode override. Use review to force review/synthesis behavior, research to force original-study behavior, or auto to infer from the task.",
                    "default": "auto",
                },
                "max_revisions": {
                    "type": "integer",
                    "description": "Maximum revision rounds per section.",
                    "default": 5,
                },
                "evaluation_threshold": {
                    "type": "number",
                    "description": "Section evaluation pass threshold from 0 to 1.",
                    "default": 0.8,
                },
                "max_context_bytes": {
                    "type": "integer",
                    "description": "Maximum context size loaded from supporting files.",
                    "default": 200000,
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session id for session-scoped output storage.",
                },
                "keep_workspace": {
                    "type": "boolean",
                    "description": "Whether to keep intermediate drafting workspace artifacts.",
                    "default": False,
                },
                "draft_only": {
                    "type": "boolean",
                    "description": "Assemble a lightweight local draft without the full staged evaluation and polish pipeline.",
                    "default": False,
                },
            },
            "required": ["task", "output_path"],
        },
    },
    "deliverable_submit": {
        # 已对齐 impl（2026-09-24 漂移合并）：描述同步 impl 的出版级指引，publish 补默认值
        "description": (
            "Submit FINAL output files to the session Deliverables panel. "
            "Use this ONLY for publication-ready artifacts:\n"
            "- Visualization plots (PNG/SVG/PDF charts, figures)\n"
            "- Summary tables (final analysis results, NOT raw data)\n"
            "- Manuscripts and reports (LaTeX, Markdown)\n"
            "- Finished code scripts that produced the above\n"
            "Do NOT submit: raw input data, intermediate CSVs, downloaded references, logs.\n"
            "Each artifact requires a path and target module (code, image_tabular, paper, refs, docs)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "publish": {
                    "type": "boolean",
                    "description": "If false, no files are copied in this call.",
                    "default": True,
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
    "execute_code": {
        # 2026-09-24 code mode：env 门控（CODE_MODE_ENABLED=1）——未开启时
        # app/services/tool_schemas.py 在注册表构建期跳过本条目，golden-master 零变更；
        # 开启时生成器在 description 尾部追加按 allowlist 动态生成的签名列表。
        # 基础文案镜像 tool_box/tools_impl/execute_code/tool.py 的 BASE_DESCRIPTION（有意保持同步）。
        "description": (
            "Run Python that calls GAgent tools programmatically in a PERSISTENT kernel. "
            "Use when you need 3+ tool calls with logic between them: loops over "
            "pages/files/accessions, filtering or reducing large tool outputs BEFORE "
            "they enter your context, branching, or retries. Use a normal tool call "
            "for a single call or results you must reason over in full. "
            "Division of labor: code_executor DELEGATES an agentic coding task to the "
            "pi coding harness (it writes and debugs the code); execute_code is YOU "
            "writing Python directly that calls tools as functions — prefer it for "
            "programmatic fan-out over tool results, not for general software tasks. "
            "The kernel keeps variables, imports, and loaded data across execute_code "
            "calls (pass reset=true to start fresh); a timed-out or interrupted call "
            "kills the kernel and LOSES that state — the result's kernel metadata "
            "(reused, execution_count, state_reset) always tells the truth about it. "
            "Tools are importable Python functions, e.g. "
            "`from gagent_tools import web_search`; each returns an ALREADY-PARSED "
            "dict — never json.loads() it. "
            "Limits: 5-minute cell timeout, max 50 tool calls per cell, stdout shown "
            "up to 50KB (head/tail; the full text is auto-saved to a file whose path "
            "rides in the result). The kernel cannot see host env secrets by design. "
            "Available functions (from gagent_tools import ...):"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python source for one cell. Tool calls are plain function calls: "
                        "`from gagent_tools import web_search` then `web_search(query=...)`; "
                        "results are already-parsed dicts. Variables survive across cells."
                    ),
                },
                "reset": {
                    "type": "boolean",
                    "description": (
                        "If true, discard the current kernel (all in-memory state) and "
                        "start a fresh one before running this cell."
                    ),
                    "default": False,
                },
            },
            "required": ["code"],
        },
    },
    "delegate_task": {
        # 2026-09-25 子 Agent 委派面 S2：env 门控（DELEGATE_TASK_ENABLED=1）——未开启时
        # app/services/tool_schemas.py 在注册表构建期跳过本条目，golden-master 零变更。
        # 描述与参数镜像 tool_box/tools_impl/delegate_task.py 的基础定义（有意保持同步，
        # 由 test_native_tool_schemas.py 的漂移锁 test_drift_merged_entries_track_impl_truth 钉住零漂移）。
        "description": (
            "Hand one self-contained, long-horizon workflow to an ISOLATED sub-agent and "
            "get back only a summary plus artifact paths. The sub-agent runs in its own "
            "session with its own tool loop: it does not see this conversation, and its "
            "transcript never enters your context. Use it when the work is long, "
            "self-contained, and you do not need to watch the intermediate steps "
            "(multi-file refactors, audit-and-repair passes, bulk literature or accession "
            "sweeps, 'take this dataset and produce X end to end'). "
            "Division of labor: delegate_task (this tool) hands off a whole GOAL and shows "
            "only the result; code_executor hands off a CODING task to the pi coding "
            "harness (it writes and debugs the code) and returns its execution result; "
            "execute_code is YOU writing Python that calls tools as functions in a kernel "
            "you keep using. "
            "Do NOT use delegate_task when: one tool call or two already answers the "
            "question (call them directly); you must read or judge the intermediate "
            "results yourself (use the tools, or execute_code when you need fan-out); you "
            "need to reuse the current kernel's state (use execute_code); the goal is "
            "'write code that does X' and you want the code back (use code_executor); the "
            "goal is read-only checking, counting/printing, or verifying results you "
            "already have (that takes seconds here and a whole agent run there); or the "
            "goal depends on implicit context from this conversation that you cannot write "
            "down in goal. Calls run one at a time — there is no parallel fan-out. "
            "Good: goal='Audit every Python file under data/pipeline for calls to the "
            "removed pandas.append API, fix them, and report the changed files', "
            "deliverable='patched files + a markdown report listing every change', "
            "context_paths=['data/pipeline']. "
            "Bad: goal='count the rows in data/submissions.csv' — file_operations or "
            "result_interpreter answers that in one call, while delegating pays a full "
            "agent run for a one-line answer. "
            "Returns summary (hard-capped), artifact_paths, usage, and trace_ref; raw CLI "
            "stdout/stderr is never returned, so inspect the run through trace_ref "
            "(run id plus absolute log paths) when you really need the detail."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "What must be accomplished, written as a self-contained brief: the "
                        "sub-agent cannot see this conversation, so name the inputs, the "
                        "expected outcome, and any constraint that matters."
                    ),
                },
                "deliverable": {
                    "type": "string",
                    "description": (
                        "Optional: the shape of the expected result and how you will accept "
                        "it (files, formats, must-cover points). Free text; it is passed to "
                        "the sub-agent verbatim."
                    ),
                },
                "context_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": (
                        "Optional directories the sub-agent may READ (absolute or "
                        "project-root-relative). Pass directories, not single files; "
                        "non-directory or non-existent entries are ignored by the runtime."
                    ),
                },
            },
            "required": ["goal"],
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
