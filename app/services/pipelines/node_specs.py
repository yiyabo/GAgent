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
def _evalue(default: float, help_en: str, help_zh: str) -> Param:
    return Param("evalue", "number", _t("E-value cutoff", "E 值上限"), default=default, minimum=0.0, maximum=1000.0,
                 help=_t(help_en, help_zh))


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
    NodeSpec(
        type="seqkit.filter_length",
        category="sequence",
        kind="tool",
        title=_t("Filter by length (SeqKit)", "按长度过滤（SeqKit）"),
        description=_t(
            "Keeps the nucleotide sequences within a length range, e.g. to drop short contigs before a tool that needs several kb.",
            "保留长度在范围内的核酸序列，例如在需要几 kb 输入的工具之前去掉短重叠群。",
        ),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(
            OutputPort(
                "sequences",
                "fasta_nt",
                _t("Kept sequences", "保留的序列"),
                "file",
                "filtered.fasta",
                reading="The sequences that passed the length filter. An empty file means none did.",
            ),
        ),
        params=(
            Param("min_length", "integer", _t("Minimum length (bp)", "最短长度（bp）"), default=0, minimum=0, maximum=100000000,
                  help=_t("Drop sequences shorter than this. 0 = no lower limit.", "丢弃短于此长度的序列。0 表示不设下限。")),
            Param("max_length", "integer", _t("Maximum length (bp)", "最长长度（bp）"), default=-1, minimum=-1, maximum=100000000,
                  help=_t("Drop sequences longer than this. -1 = no upper limit.", "丢弃长于此长度的序列。-1 表示不设上限。")),
        ),
        tool="seqkit",
        operation="seq",
        fixed_params={"output": "filtered.fasta"},
    ),
    NodeSpec(
        type="seqkit.grep",
        category="sequence",
        kind="tool",
        title=_t("Pick a sequence by ID (SeqKit)", "按 ID 选序列（SeqKit）"),
        description=_t(
            "Keeps the nucleotide sequence whose ID (the first word of the FASTA header) matches exactly.",
            "保留 ID（FASTA 标题行的第一个词）完全匹配的核酸序列。",
        ),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(
            OutputPort(
                "sequences",
                "fasta_nt",
                _t("Selected sequence", "选出的序列"),
                "file",
                "selected.fasta",
                reading="The sequence with that ID. An empty file means no sequence has this exact ID.",
            ),
        ),
        params=(
            Param("pattern", "string", _t("Sequence ID", "序列 ID"), required=True, max_length=200,
                  help=_t("Exact ID, e.g. NC_001416.1 or contig_3.", "完整的 ID，例如 NC_001416.1 或 contig_3。")),
        ),
        tool="seqkit",
        operation="grep",
        fixed_params={"output": "selected.fasta"},
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
        params=(
            Param(
                "min_score", "number", _t("Minimum score", "最低得分"), default=0.7, minimum=0.0, maximum=1.0,
                help=_t(
                    "Sequences scoring below this are not called virus or plasmid (geNomad's default is 0.7). Lower it to catch divergent phages, raise it for fewer false positives.",
                    "得分低于此值的序列不会被判为病毒或质粒（geNomad 默认 0.7）。调低可找回较分歧的噬菌体，调高则假阳性更少。",
                ),
            ),
            Param(
                "preset", "enum", _t("Filtering preset", "过滤预设"), default="default",
                choices=("default", "conservative", "relaxed"),
                help=_t(
                    "conservative drops weakly supported calls (fewer false positives); relaxed keeps every call. geNomad cannot combine a preset with a score threshold, so the minimum score only applies with default.",
                    "conservative 会去掉证据较弱的判定（假阳性更少）；relaxed 保留所有判定。geNomad 不允许预设和得分阈值同时使用，所以最低得分只在 default 时生效。",
                ),
            ),
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
            Param(
                "min_score", "number", _t("Minimum score", "最低得分"), default=0.5, minimum=0.0, maximum=1.0,
                help=_t("Sequences scoring below this are dropped as non-viral. Default 0.5.", "得分低于此值的序列被当作非病毒丢弃。默认 0.5。"),
            ),
            Param(
                "min_length", "integer", _t("Minimum length (bp)", "最短长度（bp）"), default=0, minimum=0, maximum=1000000,
                help=_t("Skip sequences shorter than this. 0 keeps everything; VirSorter2 scores short fragments unreliably.",
                        "跳过短于此长度的序列。0 表示不过滤；VirSorter2 对很短的片段打分不可靠。"),
            ),
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
    NodeSpec(
        type="genomad.find_proviruses",
        category="virus",
        kind="tool",
        title=_t("Find prophages (geNomad)", "找前噬菌体（geNomad）"),
        description=_t(
            "Finds integrated prophages in a bacterial genome and extracts them as sequences.",
            "在细菌基因组中找整合的前噬菌体，并把它们提取成序列。",
        ),
        inputs=(InputPort("genome", _NT, _t("Bacterial genome", "细菌基因组")),),
        outputs=(
            OutputPort(
                "proviruses",
                "fasta_nt",
                _t("Prophage sequences", "前噬菌体序列"),
                "glob",
                "genomad_provirus/*_find_proviruses/*_provirus.fna",
                optional=True,
                reading="One record per prophage region geNomad found. Not produced when the genome has none.",
            ),
            OutputPort(
                "table",
                "table",
                _t("Prophage table", "前噬菌体表"),
                "glob",
                "genomad_provirus/*_find_proviruses/*_provirus.tsv",
                optional=True,
                reading="One row per prophage region with its coordinates in the host sequence. Not produced when there are none.",
            ),
            OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "genomad_provirus"),
        ),
        tool="genomad",
        operation="find_proviruses",
        fixed_params={"output": "genomad_provirus"},
        slow=True,
    ),
    NodeSpec(
        type="genomad.annotate",
        category="annotation",
        kind="tool",
        title=_t("Gene annotation (geNomad)", "基因注释（geNomad）"),
        description=_t(
            "Predicts genes and annotates them against geNomad's viral marker database, without classifying the sequence.",
            "预测基因，并用 geNomad 的病毒标记数据库注释，不做序列分类。",
        ),
        inputs=(InputPort("sequences", _NT, _t("Sequences", "序列")),),
        outputs=(
            OutputPort(
                "genes",
                "table",
                _t("Gene table", "基因表"),
                "glob",
                "genomad_annotate/*_annotate/*_genes.tsv",
                reading="One row per predicted gene with its annotation, taxonomy hit and whether it is a viral or plasmid marker.",
            ),
            OutputPort("results", "directory", _t("All results", "全部结果"), "dir", "genomad_annotate"),
        ),
        tool="genomad",
        operation="annotate",
        fixed_params={"output": "genomad_annotate"},
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
        params=(
            _evalue(10, "Keep hits at or below this E-value. BLAST's default is 10 (lists weak hits); 1e-5 keeps significant hits only.",
                    "只保留 E 值不高于此值的比对。BLAST 默认 10（会列出很弱的比对）；1e-5 只留显著比对。"),
            Param("max_target_seqs", "integer", _t("Hits per query", "每条查询保留的命中数"), default=500, minimum=1, maximum=100000,
                  help=_t("Maximum number of database sequences reported per query.", "每条查询最多报告的数据库序列数。")),
            Param("qcov_hsp_perc", "number", _t("Minimum query coverage (%)", "最低查询覆盖度（%）"), default=0, minimum=0.0, maximum=100.0,
                  help=_t("Drop hits that cover less than this percent of the query. 0 = no filter.", "丢弃覆盖查询序列不足此百分比的比对。0 表示不过滤。")),
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
        params=(
            Param("task", "enum", _t("Program", "算法"), default="megablast",
                  choices=("megablast", "dc-megablast", "blastn", "blastn-short"),
                  help=_t("megablast finds near-identical sequences (fast); dc-megablast and blastn are progressively more sensitive for related but diverged genomes; blastn-short is for queries under 50 nt.",
                          "megablast 找近乎相同的序列（快）；dc-megablast 和 blastn 对亲缘较远的基因组依次更灵敏；blastn-short 用于短于 50 nt 的查询。")),
            _evalue(10, "Keep hits at or below this E-value. BLAST's default is 10; 1e-5 keeps significant hits only.",
                    "只保留 E 值不高于此值的比对。BLAST 默认 10；1e-5 只留显著比对。"),
            Param("max_target_seqs", "integer", _t("Hits per query", "每条查询保留的命中数"), default=500, minimum=1, maximum=100000,
                  help=_t("Maximum number of database sequences reported per query.", "每条查询最多报告的数据库序列数。")),
            Param("perc_identity", "number", _t("Minimum identity (%)", "最低一致性（%）"), default=0, minimum=0.0, maximum=100.0,
                  help=_t("Drop hits below this percent identity. 0 = no filter.", "丢弃一致性低于此百分比的比对。0 表示不过滤。")),
            Param("qcov_hsp_perc", "number", _t("Minimum query coverage (%)", "最低查询覆盖度（%）"), default=0, minimum=0.0, maximum=100.0,
                  help=_t("Drop hits that cover less than this percent of the query. 0 = no filter.", "丢弃覆盖查询序列不足此百分比的比对。0 表示不过滤。")),
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
        params=(
            Param("sensitivity", "number", _t("Sensitivity", "灵敏度"), default=5.7, minimum=1.0, maximum=7.5,
                  help=_t("MMseqs2 -s: 1 is fastest, 7.5 most sensitive; default 5.7. Raise it to find remote homologs.",
                          "MMseqs2 的 -s：1 最快，7.5 最灵敏；默认 5.7。调高可找到远缘同源序列。")),
            _evalue(0.001, "Keep hits at or below this E-value. MMseqs2's default is 0.001; raise it (e.g. 10) to list weak hits as blastp does.",
                    "只保留 E 值不高于此值的比对。MMseqs2 默认 0.001；调高（如 10）可像 blastp 一样列出弱比对。"),
            Param("min_seq_id", "number", _t("Minimum identity (0-1)", "最低一致性（0-1）"), default=0, minimum=0.0, maximum=1.0,
                  help=_t("Drop hits below this sequence identity. 0 = no filter.", "丢弃序列一致性低于此值的比对。0 表示不过滤。")),
            Param("min_coverage", "number", _t("Minimum coverage (0-1)", "最低覆盖度（0-1）"), default=0, minimum=0.0, maximum=1.0,
                  help=_t("Minimum fraction of both query and target an alignment must cover. 0 = no filter.",
                          "比对需同时覆盖查询和目标序列的最小比例。0 表示不过滤。")),
        ),
        tool="mmseqs2",
        operation="easy_search",
        fixed_params={"output": "mmseqs_hits.m8", "search_type": 1},
    ),
    NodeSpec(
        type="mmseqs2.cluster_proteins",
        category="compare",
        kind="tool",
        title=_t("Cluster proteins (MMseqs2)", "蛋白聚类（MMseqs2）"),
        description=_t(
            "Groups similar proteins and keeps one representative per group, e.g. to dereplicate proteins from several phages.",
            "把相似的蛋白分组，每组留一条代表序列，例如给多个噬菌体的蛋白去冗余。",
        ),
        inputs=(InputPort("proteins", _AA, _t("Proteins", "蛋白序列")),),
        outputs=(
            OutputPort(
                "representatives",
                "fasta_aa",
                _t("Representative proteins", "代表蛋白"),
                "file",
                "clusters_rep_seq.fasta",
                reading="One record per cluster, so the record count is the number of protein clusters.",
            ),
            OutputPort(
                "clusters",
                "table",
                _t("Cluster membership", "聚类成员表"),
                "file",
                "clusters_cluster.tsv",
                reading="Two columns: the cluster representative and one member; a representative also lists itself.",
            ),
        ),
        params=(
            Param("min_seq_id", "number", _t("Minimum identity (0-1)", "最低一致性（0-1）"), default=0.9, minimum=0.0, maximum=1.0,
                  help=_t("Proteins at least this identical, over most of their length, share a cluster. Default 0.9.",
                          "一致性不低于此值（在大部分长度上）的蛋白归为一组。默认 0.9。")),
        ),
        tool="mmseqs2",
        operation="easy_linclust",
        fixed_params={"output": "clusters"},
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
        params=(
            Param("frag_len", "integer", _t("Fragment length (bp)", "片段长度（bp）"), default=3000, minimum=160, maximum=100000,
                  help=_t("FastANI cuts the query into fragments of this length. The default 3000 suits bacteria; use 500-1000 for short phage genomes.",
                          "FastANI 把查询序列切成此长度的片段。默认 3000 适合细菌；短的噬菌体基因组可用 500-1000。")),
            Param("min_fraction", "number", _t("Minimum shared fraction (0-1)", "最低共享比例（0-1）"), default=0.2, minimum=0.0, maximum=1.0,
                  help=_t("Report a pair only if at least this fraction of the genome is shared (default 0.2).",
                          "只有基因组共享比例不低于此值才报告（默认 0.2）。")),
            Param("kmer", "integer", _t("k-mer size", "k-mer 大小"), default=16, minimum=4, maximum=16,
                  help=_t("At most 16. Smaller values (e.g. 14) are more sensitive for distant genomes.", "最大 16。较小的值（如 14）对亲缘较远的基因组更灵敏。")),
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
        params=(
            _evalue(10, "Report sequences at or below this E-value. HMMER's default is 10; use 1e-5 for significant hits only.",
                    "报告 E 值不高于此值的序列。HMMER 默认 10；只要显著命中可用 1e-5。"),
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
