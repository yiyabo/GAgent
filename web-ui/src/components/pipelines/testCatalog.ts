import type { InputPortSpec, NodeCatalog, NodeTypeSpec, OutputPortSpec, ParamSpec, PipelineDetail, PipelineGraph } from '@api/pipelines';

/** A small catalog in the server's shape, for unit tests. The real one is served by /pipelines/node-types. */

const tx = (en: string, zh: string) => ({ en, zh });
const input = (name: string, types: string[], title: [string, string], multiple = false): InputPortSpec => ({
  name,
  types,
  title: tx(...title),
  required: true,
  multiple,
});
const output = (name: string, type: string, title: [string, string]): OutputPortSpec => ({ name, type, title: tx(...title), optional: false });
const param = (item: Omit<ParamSpec, 'title' | 'required' | 'default'> & { title: [string, string]; required?: boolean; default?: unknown }): ParamSpec => ({
  ...item,
  title: tx(...item.title),
  required: item.required ?? false,
  default: item.default ?? null,
});

function spec(item: Partial<NodeTypeSpec> & Pick<NodeTypeSpec, 'type' | 'kind' | 'category'>): NodeTypeSpec {
  return {
    title: tx(item.type, item.type),
    description: tx('', ''),
    tool: null,
    operation: null,
    slow: false,
    terminal: false,
    inputs: [],
    outputs: [],
    params: [],
    ...item,
  };
}

export const TEST_CATALOG: NodeCatalog = {
  spec_version: 1,
  any_type: 'any',
  data_types: [
    { id: 'fasta_nt', title: tx('Nucleotide FASTA', '核酸 FASTA') },
    { id: 'fasta_aa', title: tx('Protein FASTA', '蛋白 FASTA') },
    { id: 'table', title: tx('Table', '表格') },
    { id: 'blast_db_aa', title: tx('BLAST protein database', 'BLAST 蛋白库') },
    { id: 'report', title: tx('Report', '报告') },
  ],
  categories: [
    { id: 'input', title: tx('Inputs', '输入') },
    { id: 'ncbi', title: tx('NCBI download', 'NCBI 下载') },
    { id: 'genes', title: tx('Genes', '基因') },
    { id: 'compare', title: tx('Comparison', '比对与比较') },
    { id: 'report', title: tx('Report', '报告') },
  ],
  node_types: [
    spec({
      type: 'input.genome',
      kind: 'input',
      category: 'input',
      title: tx('Genome / contigs', '基因组／重叠群'),
      outputs: [output('file', 'fasta_nt', ['Nucleotide FASTA', '核酸 FASTA'])],
      params: [param({ name: 'path', kind: 'path', title: ['File', '文件'] })],
    }),
    spec({
      type: 'ncbi.genome',
      kind: 'ncbi',
      category: 'ncbi',
      title: tx('NCBI genome download', 'NCBI 基因组下载'),
      outputs: [output('genome', 'fasta_nt', ['Genome FASTA', '基因组 FASTA'])],
      params: [param({ name: 'accessions', kind: 'accessions', title: ['Accessions', '登录号'], required: true, max_length: 2000 })],
    }),
    spec({
      type: 'prodigal.predict',
      kind: 'tool',
      category: 'genes',
      tool: 'prodigal',
      operation: 'predict',
      title: tx('Gene prediction (Prodigal)', '基因预测（Prodigal）'),
      inputs: [input('genome', ['fasta_nt'], ['Genome', '基因组'])],
      outputs: [output('proteins', 'fasta_aa', ['Proteins', '蛋白序列']), output('genes', 'fasta_nt', ['Gene sequences', '基因序列'])],
      params: [
        param({ name: 'mode', kind: 'enum', title: ['Mode', '模式'], default: 'meta', choices: ['meta', 'single'] }),
        param({ name: 'translation_table', kind: 'integer', title: ['Translation table', '密码子表'], default: 11, minimum: 1, maximum: 33 }),
      ],
    }),
    spec({
      type: 'blast.makeblastdb_aa',
      kind: 'tool',
      category: 'compare',
      tool: 'blast',
      operation: 'makeblastdb',
      title: tx('BLAST protein database', '建 BLAST 蛋白库'),
      inputs: [input('proteins', ['fasta_aa'], ['Proteins', '蛋白序列'])],
      outputs: [output('db', 'blast_db_aa', ['BLAST protein database', 'BLAST 蛋白库'])],
    }),
    spec({
      type: 'blast.blastp',
      kind: 'tool',
      category: 'compare',
      tool: 'blast',
      operation: 'blastp',
      title: tx('Protein search (blastp)', '蛋白比对（blastp）'),
      inputs: [input('query', ['fasta_aa'], ['Query proteins', '查询蛋白']), input('db', ['blast_db_aa'], ['Database', '数据库'])],
      outputs: [output('hits', 'table', ['Hits', '比对结果'])],
    }),
    spec({
      type: 'agent.report',
      kind: 'agent',
      category: 'report',
      terminal: true,
      title: tx('Agent report', 'Agent 报告'),
      inputs: [input('evidence', ['any'], ['Results to read', '要读取的结果'], true)],
      outputs: [output('report', 'report', ['Report', '报告'])],
      params: [param({ name: 'language', kind: 'enum', title: ['Language', '语言'], default: 'zh', choices: ['zh', 'en'] })],
    }),
  ],
  templates: [
    {
      id: 'phage_lambda_t7',
      title: tx('Phage basics: lambda vs T7', '噬菌体基础分析：lambda 对比 T7'),
      description: tx('Downloads lambda and T7 and compares them.', '下载 lambda 和 T7 并比较。'),
      node_count: 17,
    },
  ],
};

/** download -> prodigal -> (makeblastdb, blastp) -> report */
export function chainGraph(): PipelineGraph {
  return {
    schema: 1,
    nodes: {
      dl: { type: 'ncbi.genome', label: 'lambda', params: { accessions: 'NC_001416.1' } },
      genes: { type: 'prodigal.predict', inputs: { genome: { node: 'dl', port: 'genome' } } },
      db: { type: 'blast.makeblastdb_aa', inputs: { proteins: { node: 'genes', port: 'proteins' } } },
      hits: { type: 'blast.blastp', inputs: { query: { node: 'genes', port: 'proteins' }, db: { node: 'db', port: 'db' } } },
      report: { type: 'agent.report', inputs: { evidence: [{ node: 'hits', port: 'hits' }] } },
    },
    layout: {},
  };
}

export function pipelineDetail(overrides: Partial<PipelineDetail> = {}): PipelineDetail {
  return {
    id: 'pl1',
    owner_id: 'u1',
    name: 'Lambda basics',
    description: '',
    latest_version: 0,
    created_at: '2026-10-24 08:00:00',
    updated_at: '2026-10-24 08:00:00',
    last_run_status: null,
    draft: chainGraph(),
    versions: [],
    active_runs: 0,
    validation: { ok: true, errors: [], warnings: [], order: [] },
    ...overrides,
  };
}
