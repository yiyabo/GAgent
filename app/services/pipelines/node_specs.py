"""The node types a pipeline can use: typed ports, parameters and how each one runs.

A tool node runs exactly one ``bio_tools`` operation. The names of the files it writes are fixed here, not
chosen by the user, so the engine always knows which file of a run feeds which output port. Titles are
bilingual data served to the editor, which shows them in the user's language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Bump when a node's command, fixed parameters or outputs change: the cache key includes it, so results
# computed by an older definition are not reused.
SPEC_VERSION = 1

ANY = "any"


def _t(en: str, zh: str) -> Dict[str, str]:
    return {"en": en, "zh": zh}


DATA_TYPES: Dict[str, Dict[str, str]] = {
    "fasta_nt": _t("Nucleotide FASTA", "核酸 FASTA"),
    "fasta_aa": _t("Protein FASTA", "蛋白 FASTA"),
    "gff": _t("GFF annotation", "GFF 注释"),
    "table": _t("Table", "表格"),
    "directory": _t("Result folder", "结果目录"),
    "blast_db_nt": _t("BLAST nucleotide database", "BLAST 核酸库"),
    "blast_db_aa": _t("BLAST protein database", "BLAST 蛋白库"),
    "hmm": _t("HMM profile", "HMM 模型"),
    "report": _t("Report", "报告"),
}

CATEGORIES: Dict[str, Dict[str, str]] = {
    "input": _t("Inputs", "输入"),
    "ncbi": _t("NCBI download", "NCBI 下载"),
    "sequence": _t("Sequences", "序列"),
    "genes": _t("Genes", "基因"),
    "virus": _t("Virus identification and quality", "病毒鉴定与质量"),
    "phage": _t("Phage traits", "噬菌体特性"),
    "compare": _t("Comparison", "比对与比较"),
    "annotation": _t("Annotation", "注释"),
    "report": _t("Report", "报告"),
}


@dataclass(frozen=True)
class InputPort:
    name: str
    types: Tuple[str, ...]
    title: Dict[str, str]
    required: bool = True
    multiple: bool = False
    # bio_tools parameter that receives the upstream path; None means the call's ``input_file``.
    param: Optional[str] = None


@dataclass(frozen=True)
class OutputPort:
    name: str
    type: str
    title: Dict[str, str]
    # file: ``source`` is a path in the run's work directory; prefix: a database prefix there;
    # dir: a directory there; glob: the first match of ``source`` there; stdout: the run's standard output;
    # fetched: the file a download wrote; report: the report the agent node wrote.
    kind: str
    source: str = ""
    optional: bool = False
    # For the report node only: how to read this output, above all what an empty or absent file means.
    reading: str = ""
    # How the report node condenses the file instead of quoting its beginning ("blast_tab": hit statistics).
    digest: str = ""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # string | text | integer | number | enum | path | accessions
    title: Dict[str, str]
    default: Any = None
    required: bool = False
    choices: Tuple[str, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    max_length: int = 2000
    help: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class NodeSpec:
    type: str
    category: str
    kind: str  # input | ncbi | tool | agent
    title: Dict[str, str]
    description: Dict[str, str]
    inputs: Tuple[InputPort, ...] = ()
    outputs: Tuple[OutputPort, ...] = ()
    params: Tuple[Param, ...] = ()
    tool: str = ""
    operation: str = ""
    # bio_tools parameters the engine always sets (output names, fixed modes).
    fixed_params: Mapping[str, Any] = field(default_factory=dict)
    slow: bool = False
    terminal: bool = False
    cacheable: bool = True

    def input_port(self, name: str) -> Optional[InputPort]:
        return next((port for port in self.inputs if port.name == name), None)

    def output_port(self, name: str) -> Optional[OutputPort]:
        return next((port for port in self.outputs if port.name == name), None)

    def param(self, name: str) -> Optional[Param]:
        return next((item for item in self.params if item.name == name), None)


def _file_input(type_name: str, title: Dict[str, str], description: Dict[str, str], data_type: str) -> NodeSpec:
    return NodeSpec(
        type=type_name,
        category="input",
        kind="input",
        title=title,
        description=description,
        outputs=(OutputPort("file", data_type, DATA_TYPES[data_type], "file"),),
        params=(
            Param(
                "path",
                "path",
                _t("File", "文件"),
                help=_t("Upload a file to this pipeline, then pick it here.", "先把文件上传到这条流水线，再在这里选择。"),
            ),
        ),
    )


_NT = ("fasta_nt",)
_AA = ("fasta_aa",)
_BLAST_READING = (
    "BLAST tabular (outfmt 6). BLAST keeps hits up to E-value 10 by default, so weak hits are listed; an E-value "
    "above about 1e-5 is usually not significant. Empty means no hits."
)
_THREADS_NOTE = _t("Uses the CPUs the scheduler gives the job.", "使用调度器分配给作业的 CPU。")

NODE_SPECS: Tuple[NodeSpec, ...] = (
    # ------------------------------------------------------------------ inputs
    _file_input(
        "input.genome",
        _t("Genome / contigs", "基因组／重叠群"),
        _t("A nucleotide FASTA file you uploaded.", "你上传的核酸 FASTA 文件。"),
        "fasta_nt",
    ),
    _file_input(
        "input.proteins",
        _t("Proteins", "蛋白序列"),
        _t("A protein FASTA file you uploaded.", "你上传的蛋白 FASTA 文件。"),
        "fasta_aa",
    ),
    _file_input(
        "input.hmm",
        _t("HMM profile", "HMM 模型"),
        _t("An HMMER profile (.hmm) you uploaded, for hmmsearch.", "你上传的 HMMER 模型文件（.hmm），供 hmmsearch 使用。"),
        "hmm",
    ),
    # ------------------------------------------------------------------ NCBI
    NodeSpec(
        type="ncbi.genome",
        category="ncbi",
        kind="ncbi",
        title=_t("NCBI genome download", "NCBI 基因组下载"),
        description=_t(
            "Downloads nucleotide FASTA for the given accessions.", "按登录号下载核酸 FASTA。"
        ),
        outputs=(
            OutputPort(
                "genome",
                "fasta_nt",
                _t("Genome FASTA", "基因组 FASTA"),
                "fetched",
                reading="The nucleotide records downloaded from NCBI nuccore.",
            ),
        ),
        params=(
            Param(
                "accessions",
                "accessions",
                _t("Accessions", "登录号"),
                required=True,
                help=_t("Comma separated, e.g. NC_001416.1, NC_001604.1", "用逗号分隔，例如 NC_001416.1, NC_001604.1"),
            ),
        ),
        fixed_params={"format": "fasta", "database": "nuccore"},
    ),
    NodeSpec(
        type="ncbi.cds_proteins",
        category="ncbi",
        kind="ncbi",
        title=_t("NCBI CDS proteins", "NCBI CDS 蛋白"),
        description=_t(
            "Downloads every annotated CDS of the given nucleotide records as protein FASTA.",
            "下载这些核酸记录里全部注释 CDS 的蛋白 FASTA。",
        ),
        outputs=(
            OutputPort(
                "proteins",
                "fasta_aa",
                _t("Proteins", "蛋白序列"),
                "fetched",
                reading="The translations of the CDS features annotated in the NCBI records: one record per annotated CDS.",
            ),
        ),
        params=(
            Param(
                "accessions",
                "accessions",
                _t("Accessions", "登录号"),
                required=True,
                help=_t("Comma separated nucleotide accessions", "用逗号分隔的核酸登录号"),
            ),
        ),
        fixed_params={"format": "fasta_cds_aa", "database": "nuccore"},
    ),
    # ------------------------------------------------------------------ sequences
    NodeSpec(
        type="seqkit.stats",
        category="sequence",
        kind="tool",
        title=_t("Sequence statistics (SeqKit)", "序列统计（SeqKit）"),
        description=_t("Counts, lengths and GC content.", "序列条数、长度和 GC 含量。"),
        inputs=(InputPort("sequences", ("fasta_nt", "fasta_aa"), _t("Sequences", "序列")),),
        outputs=(
            OutputPort(
                "stats",
                "table",
                _t("Statistics", "统计表"),
                "stdout",
                "seqkit_stats.txt",
                reading="SeqKit stats: one row per input file with num_seqs, sum_len, min_len, avg_len and max_len. It has no GC column.",
            ),
        ),
        tool="seqkit",
        operation="stats",
    ),
    # ------------------------------------------------------------------ genes
    NodeSpec(
        type="prodigal.predict",
        category="genes",
        kind="tool",
        title=_t("Gene prediction (Prodigal)", "基因预测（Prodigal）"),
        description=_t("Predicts protein-coding genes.", "预测蛋白编码基因。"),
        inputs=(InputPort("genome", _NT, _t("Genome", "基因组")),),
        outputs=(
            OutputPort(
                "proteins",
                "fasta_aa",
                _t("Proteins", "蛋白序列"),
                "file",
                "proteins.faa",
                reading="Prodigal: one record per predicted protein-coding gene, so the record count is the number of genes it predicted.",
            ),
            OutputPort("genes", "fasta_nt", _t("Gene sequences", "基因序列"), "file", "genes.fna"),
            OutputPort("gff", "gff", _t("Gene coordinates", "基因坐标"), "file", "genes.gff"),
        ),
        params=(
            Param(
                "mode",
                "enum",
                _t("Mode", "模式"),
                default="meta",
                choices=("meta", "single"),
                help=_t(
                    "meta works for short or mixed sequences such as phage genomes; single trains on one larger genome.",
                    "meta 适合噬菌体等较短或混合的序列；single 用一条较大的基因组自行训练。",
                ),
            ),
            Param(
                "translation_table",
                "integer",
                _t("Translation table", "密码子表"),
                default=11,
                minimum=1,
                maximum=33,
            ),
        ),
        tool="prodigal",
        operation="predict",
        fixed_params={"protein": "proteins.faa", "nucleotide": "genes.fna", "gff": "genes.gff"},
    ),
    # ------------------------------------------------------------------ virus identification and quality
    NodeSpec(
        type="genomad.end_to_end",
        category="virus",
        kind="tool",
        title=_t("Virus and plasmid identification (geNomad)", "病毒与质粒鉴定（geNomad）"),
        description=_t("Classifies sequences as virus, plasmid or chromosome, with taxonomy.", "把序列判为病毒、质粒或染色体，并给出分类。"),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(
            OutputPort(
                "virus_summary",
                "table",
                _t("Virus summary", "病毒汇总表"),
                "glob",
                "genomad_out/*_summary/*_virus_summary.tsv",
                optional=True,
                reading=(
                    "geNomad: one row per sequence it calls viral, with virus_score (0-1), n_genes, n_hallmarks, topology "
                    "(DTR = direct terminal repeats) and the taxonomy lineage from geNomad's own marker database. "
                    "Not produced when no sequence is called viral."
                ),
            ),
            OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "genomad_out"),
        ),
        tool="genomad",
        operation="end_to_end",
        fixed_params={"output": "genomad_out"},
        slow=True,
    ),
    NodeSpec(
        type="checkv.end_to_end",
        category="virus",
        kind="tool",
        title=_t("Virus genome quality (CheckV)", "病毒基因组质量（CheckV）"),
        description=_t("Completeness, contamination and quality tier.", "完整度、污染和质量等级。"),
        inputs=(InputPort("sequences", _NT, _t("Viral sequences", "病毒序列")),),
        outputs=(
            OutputPort(
                "quality",
                "table",
                _t("Quality summary", "质量汇总表"),
                "file",
                "checkv_out/quality_summary.tsv",
                reading=(
                    "CheckV: one row per sequence; checkv_quality is Complete, High-quality, Medium-quality, Low-quality or "
                    "Not-determined; completeness and contamination are percentages; completeness_method says how it was estimated."
                ),
            ),
            OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "checkv_out"),
        ),
        tool="checkv",
        operation="end_to_end",
        fixed_params={"output": "checkv_out"},
        slow=True,
    ),
    NodeSpec(
        type="virsorter2.run",
        category="virus",
        kind="tool",
        title=_t("Virus identification (VirSorter2)", "病毒鉴定（VirSorter2）"),
        description=_t("Scores sequences for viral signal.", "给序列的病毒信号打分。"),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(
            OutputPort(
                "scores",
                "table",
                _t("Viral scores", "病毒得分表"),
                "file",
                "virsorter2_out/final-viral-score.tsv",
                optional=True,
                reading="VirSorter2: one row per sequence scored as viral with max_score (0-1). Not produced when nothing scores as viral.",
            ),
            OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "virsorter2_out"),
        ),
        params=(
            Param("min_score", "number", _t("Minimum score", "最低得分"), default=0.5, minimum=0.0, maximum=1.0),
        ),
        tool="virsorter2",
        operation="run",
        fixed_params={"output": "virsorter2_out"},
        slow=True,
    ),
    NodeSpec(
        type="vibrant.run",
        category="virus",
        kind="tool",
        title=_t("Phage identification (VIBRANT)", "噬菌体鉴定（VIBRANT）"),
        description=_t(
            "Identifies phage sequences and their lifestyle; inputs shorter than 1 kb are skipped.",
            "鉴定噬菌体序列及其生活方式；短于 1 kb 的序列会被跳过。",
        ),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "vibrant_out"),),
        tool="vibrant",
        operation="run",
        fixed_params={"output": "vibrant_out"},
        slow=True,
    ),
    # ------------------------------------------------------------------ phage traits (PhaBOX2)
    *(
        NodeSpec(
            type=f"phabox2.{task}",
            category="phage",
            kind="tool",
            title=title,
            description=description,
            inputs=(InputPort("sequences", _NT, _t("Phage sequences", "噬菌体序列")),),
            outputs=(
                OutputPort(
                    "prediction",
                    "table",
                    _t("Prediction", "预测表"),
                    "file",
                    f"phabox_{task}/final_prediction/{task}_prediction.tsv",
                    reading=reading,
                ),
                OutputPort("results", "directory", _t("All results", "全部结果"), "dir", f"phabox_{task}"),
            ),
            tool="phabox2",
            operation=task,
            fixed_params={"output": f"phabox_{task}"},
            slow=True,
        )
        for task, title, description, reading in (
            (
                "phamer",
                _t("Phage identification (PhaMer)", "噬菌体鉴定（PhaMer）"),
                _t("Is it a phage?", "是不是噬菌体。"),
                "PhaMer: one row per sequence; Pred is phage or non-phage, with a score from 0 to 1.",
            ),
            (
                "phatyp",
                _t("Lifestyle (PhaTYP)", "生活方式（PhaTYP）"),
                _t("Virulent or temperate.", "烈性还是温和。"),
                "PhaTYP: one row per phage sequence; TYPE is virulent or temperate, PhaTYPScore from 0 to 1.",
            ),
            (
                "phagcn",
                _t("Taxonomy (PhaGCN)", "分类（PhaGCN）"),
                _t("Family-level classification.", "科级分类。"),
                "PhaGCN: one row per phage sequence with the predicted lineage and a score.",
            ),
            (
                "cherry",
                _t("Host prediction (CHERRY)", "宿主预测（CHERRY）"),
                _t("Predicted bacterial host.", "预测的细菌宿主。"),
                "CHERRY: one row per phage sequence with the predicted host lineage and a score.",
            ),
        )
    ),
    # ------------------------------------------------------------------ comparison
    NodeSpec(
        type="blast.makeblastdb_aa",
        category="compare",
        kind="tool",
        title=_t("BLAST protein database", "建 BLAST 蛋白库"),
        description=_t("Builds a protein BLAST database for blastp.", "建一个蛋白 BLAST 库，供 blastp 使用。"),
        inputs=(InputPort("proteins", _AA, _t("Proteins", "蛋白序列")),),
        outputs=(OutputPort("db", "blast_db_aa", DATA_TYPES["blast_db_aa"], "prefix", "blast_db"),),
        tool="blast",
        operation="makeblastdb",
        fixed_params={"type": "prot", "db": "blast_db"},
    ),
    NodeSpec(
        type="blast.makeblastdb_nt",
        category="compare",
        kind="tool",
        title=_t("BLAST nucleotide database", "建 BLAST 核酸库"),
        description=_t("Builds a nucleotide BLAST database for blastn.", "建一个核酸 BLAST 库，供 blastn 使用。"),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(OutputPort("db", "blast_db_nt", DATA_TYPES["blast_db_nt"], "prefix", "blast_db"),),
        tool="blast",
        operation="makeblastdb",
        fixed_params={"type": "nucl", "db": "blast_db"},
    ),
    NodeSpec(
        type="blast.blastp",
        category="compare",
        kind="tool",
        title=_t("Protein search (blastp)", "蛋白比对（blastp）"),
        description=_t("Searches query proteins against a protein database (tabular output).", "用查询蛋白搜索蛋白库（表格输出）。"),
        inputs=(
            InputPort("query", _AA, _t("Query proteins", "查询蛋白")),
            InputPort("db", ("blast_db_aa",), _t("Database", "数据库"), param="db"),
        ),
        outputs=(
            OutputPort(
                "hits",
                "table",
                _t("Hits", "比对结果"),
                "file",
                "blastp_hits.tsv",
                reading=_BLAST_READING,
                digest="blast_tab",
            ),
        ),
        tool="blast",
        operation="blastp",
        fixed_params={"output": "blastp_hits.tsv"},
    ),
    NodeSpec(
        type="blast.blastn",
        category="compare",
        kind="tool",
        title=_t("Nucleotide search (blastn)", "核酸比对（blastn）"),
        description=_t("Searches query sequences against a nucleotide database (tabular output).", "用查询序列搜索核酸库（表格输出）。"),
        inputs=(
            InputPort("query", _NT, _t("Query sequences", "查询序列")),
            InputPort("db", ("blast_db_nt",), _t("Database", "数据库"), param="db"),
        ),
        outputs=(
            OutputPort(
                "hits",
                "table",
                _t("Hits", "比对结果"),
                "file",
                "blastn_hits.tsv",
                reading=_BLAST_READING,
                digest="blast_tab",
            ),
        ),
        tool="blast",
        operation="blastn",
        fixed_params={"output": "blastn_hits.tsv"},
    ),
    NodeSpec(
        type="mmseqs2.protein_search",
        category="compare",
        kind="tool",
        title=_t("Protein search (MMseqs2)", "蛋白比对（MMseqs2）"),
        description=_t("Fast protein-to-protein search (tabular .m8 output).", "快速的蛋白对蛋白搜索（.m8 表格输出）。"),
        inputs=(
            InputPort("query", _AA, _t("Query proteins", "查询蛋白"), param="query"),
            InputPort("target", _AA, _t("Target proteins", "目标蛋白"), param="target"),
        ),
        outputs=(
            OutputPort(
                "hits",
                "table",
                _t("Hits", "比对结果"),
                "file",
                "mmseqs_hits.m8",
                reading=(
                    "MMseqs2 easy-search, BLAST tabular columns. It keeps hits with E-value up to 1e-3 by default, stricter "
                    "than blastp's default of 10, so it can be empty where blastp lists only weak hits. Empty means no pair "
                    "passed that cutoff, not that the tool failed."
                ),
                digest="blast_tab",
            ),
        ),
        tool="mmseqs2",
        operation="easy_search",
        fixed_params={"output": "mmseqs_hits.m8", "search_type": 1},
    ),
    NodeSpec(
        type="fastani.compare",
        category="compare",
        kind="tool",
        title=_t("Average nucleotide identity (FastANI)", "平均核苷酸一致性（FastANI）"),
        description=_t("ANI between two genomes; no output line means ANI is too low to report.", "两条基因组的 ANI；没有结果行表示 ANI 太低、无法报告。"),
        inputs=(
            InputPort("query", _NT, _t("Query genome", "查询基因组"), param="query"),
            InputPort("reference", _NT, _t("Reference genome", "参考基因组"), param="reference"),
        ),
        outputs=(
            OutputPort(
                "ani",
                "table",
                _t("ANI", "ANI 结果"),
                "file",
                "fastani.txt",
                reading=(
                    "FastANI: one line per genome pair (query, reference, ANI %, matching fragments, total query fragments). "
                    "It reports nothing for pairs below its range (about 80% ANI), so an empty file means the two genomes "
                    "are not closely related at the nucleotide level; it is a result, not a failure."
                ),
            ),
        ),
        tool="fastani",
        operation="compare",
        fixed_params={"output": "fastani.txt"},
    ),
    NodeSpec(
        type="hmmer.hmmsearch",
        category="compare",
        kind="tool",
        title=_t("Profile search (hmmsearch)", "模型搜索（hmmsearch）"),
        description=_t("Searches proteins with an HMM profile (per-sequence table).", "用 HMM 模型搜索蛋白（逐条序列结果表）。"),
        inputs=(
            InputPort("proteins", _AA, _t("Proteins", "蛋白序列")),
            InputPort("profile", ("hmm",), _t("HMM profile", "HMM 模型"), param="db"),
        ),
        outputs=(
            OutputPort(
                "hits",
                "table",
                _t("Hits", "比对结果"),
                "file",
                "hmmsearch.tblout",
                reading=(
                    "HMMER --tblout: one line per target sequence above the reporting threshold (E-value 10 by default); "
                    "lines starting with # are comments, so a file with only comments means no hits."
                ),
            ),
        ),
        tool="hmmer",
        operation="hmmsearch",
        fixed_params={"output": "hmmsearch.tblout"},
    ),
    # ------------------------------------------------------------------ annotation
    NodeSpec(
        type="bakta.annotate",
        category="annotation",
        kind="tool",
        title=_t("Genome annotation (Bakta)", "基因组注释（Bakta）"),
        description=_t("Annotates a bacterial genome (genes, products, GFF3).", "注释细菌基因组（基因、产物、GFF3）。"),
        inputs=(InputPort("genome", _NT, _t("Genome", "基因组")),),
        outputs=(
            OutputPort("proteins", "fasta_aa", _t("Proteins", "蛋白序列"), "file", "bakta_out/genome.faa"),
            OutputPort("gff", "gff", _t("Annotation (GFF3)", "注释（GFF3）"), "file", "bakta_out/genome.gff3"),
            OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "bakta_out"),
        ),
        tool="bakta",
        operation="annotate",
        fixed_params={"output": "bakta_out", "prefix": "genome"},
        slow=True,
    ),
    # ------------------------------------------------------------------ report
    NodeSpec(
        type="agent.report",
        category="report",
        kind="agent",
        title=_t("Agent report", "Agent 报告"),
        description=_t(
            "The model reads the connected results and writes a report. Only allowed at the end of a pipeline.",
            "由模型读取接入的结果并写报告。只能放在流水线末端。",
        ),
        inputs=(InputPort("evidence", (ANY,), _t("Results to read", "要读取的结果"), multiple=True),),
        outputs=(OutputPort("report", "report", DATA_TYPES["report"], "report", "report.md"),),
        params=(
            Param(
                "instructions",
                "text",
                _t("What the report should cover", "报告要回答什么"),
                default=(
                    "Summarize what each result shows, list the key numbers in a table, point out where tools "
                    "agree or disagree, and say which conclusions are uncertain."
                ),
                max_length=4000,
            ),
            Param("language", "enum", _t("Language", "语言"), default="zh", choices=("zh", "en")),
        ),
        terminal=True,
        cacheable=False,
    ),
)

_BY_TYPE: Dict[str, NodeSpec] = {spec.type: spec for spec in NODE_SPECS}


def get_spec(node_type: str) -> Optional[NodeSpec]:
    return _BY_TYPE.get(str(node_type or ""))


def accepts(port: InputPort, data_type: str) -> bool:
    return ANY in port.types or data_type in port.types


def _param_public(param: Param) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "name": param.name,
        "kind": param.kind,
        "title": param.title,
        "default": param.default,
        "required": param.required,
    }
    if param.choices:
        item["choices"] = list(param.choices)
    if param.minimum is not None:
        item["minimum"] = param.minimum
    if param.maximum is not None:
        item["maximum"] = param.maximum
    if param.kind in {"string", "text", "accessions"}:
        item["max_length"] = param.max_length
    if param.help:
        item["help"] = param.help
    return item


def public_catalog() -> Dict[str, Any]:
    """What the editor needs: data types, categories and every node type's ports and parameters."""
    nodes: List[Dict[str, Any]] = []
    for spec in NODE_SPECS:
        nodes.append(
            {
                "type": spec.type,
                "category": spec.category,
                "kind": spec.kind,
                "title": spec.title,
                "description": spec.description,
                "tool": spec.tool or None,
                "operation": spec.operation or None,
                "slow": spec.slow,
                "terminal": spec.terminal,
                "inputs": [
                    {
                        "name": port.name,
                        "types": list(port.types),
                        "title": port.title,
                        "required": port.required,
                        "multiple": port.multiple,
                    }
                    for port in spec.inputs
                ],
                "outputs": [
                    {"name": port.name, "type": port.type, "title": port.title, "optional": port.optional}
                    for port in spec.outputs
                ],
                "params": [_param_public(param) for param in spec.params],
            }
        )
    return {
        "spec_version": SPEC_VERSION,
        "any_type": ANY,
        "data_types": [{"id": key, "title": title} for key, title in DATA_TYPES.items()],
        "categories": [{"id": key, "title": title} for key, title in CATEGORIES.items()],
        "node_types": nodes,
    }
