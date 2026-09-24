/**
 * Group consecutive tool-call steps in the thinking activity stream into a
 * single collapsible summary row (Kimi-style: "检索了 1 次资料 · 读取了 2 个文件").
 *
 * Pure logic lives here so the React component stays thin and the grouping
 * rules are unit-testable.
 */

export type ToolKind =
  | 'search'
  | 'read'
  | 'code'
  | 'write'
  | 'knowledge'
  | 'plan'
  | 'analysis'
  | 'file'
  | 'tool';

/** Minimal step shape required for grouping (ThinkingStep-compatible). */
export interface GroupableStep {
  iteration: number;
  action?: string | null;
}

export interface ToolStepGroup<T extends GroupableStep> {
  id: string;
  steps: T[];
}

export type StreamRow<T extends GroupableStep> =
  | { type: 'step'; step: T }
  | { type: 'tool_group'; group: ToolStepGroup<T> };

const KIND_BY_TOOL: Record<string, ToolKind> = {
  web_search: 'search',
  literature_pipeline: 'search',
  document_reader: 'read',
  vision_reader: 'read',
  lightrag_query: 'knowledge',
  graph_rag: 'knowledge',
  code_executor: 'code',
  execute_code: 'code',
  terminal_session: 'code',
  manuscript_writer: 'write',
  review_pack_writer: 'write',
  plan_operation: 'plan',
  bio_tools: 'analysis',
  phagescope: 'analysis',
  phagescope_research: 'analysis',
  result_interpreter: 'analysis',
  scientific_figure_generator: 'analysis',
  verify_task: 'analysis',
  sequence_fetch: 'file',
  url_fetch: 'file',
  file_operations: 'file',
  load_skill: 'read',
};

/** Parse tool names out of a step's action payload (single or multi-tool). */
export function toolNamesFromAction(action?: string | null): string[] {
  if (!action) return [];
  let parsed: any;
  try {
    parsed = JSON.parse(action);
  } catch {
    return ['unknown'];
  }
  if (Array.isArray(parsed?.tools) && parsed.tools.length > 0) {
    const names = parsed.tools
      .map((item: any) => (typeof item?.tool === 'string' ? item.tool : null))
      .filter((name: string | null): name is string => Boolean(name));
    return names.length > 0 ? names : ['unknown'];
  }
  return [typeof parsed?.tool === 'string' ? parsed.tool : 'unknown'];
}

export function kindForTool(toolName: string): ToolKind {
  return KIND_BY_TOOL[toolName] || 'tool';
}

/**
 * Fold runs of >=2 consecutive tool-call steps into one group row.
 * With `keepLastSolo` (live runs) the final step is never folded, so the
 * currently-running tool call stays individually visible.
 */
export function buildStreamRows<T extends GroupableStep>(
  steps: T[],
  opts: { keepLastSolo?: boolean } = {},
): StreamRow<T>[] {
  const keepLastSolo = Boolean(opts.keepLastSolo) && steps.length > 0;
  const lastIteration = keepLastSolo ? steps[steps.length - 1].iteration : null;

  const rows: StreamRow<T>[] = [];
  let pending: T[] = [];

  const flush = () => {
    if (pending.length >= 2) {
      const first = pending[0].iteration;
      const last = pending[pending.length - 1].iteration;
      rows.push({
        type: 'tool_group',
        group: { id: `grp-${first}-${last}`, steps: [...pending] },
      });
    } else {
      for (const step of pending) rows.push({ type: 'step', step });
    }
    pending = [];
  };

  for (const step of steps) {
    const isToolStep = Boolean(step.action) && step.iteration !== lastIteration;
    if (isToolStep) {
      pending.push(step);
    } else {
      flush();
      rows.push({ type: 'step', step });
    }
  }
  flush();
  return rows;
}

const ZH_PHRASE: Record<ToolKind, (n: number) => string> = {
  search: (n) => `检索了 ${n} 次资料`,
  read: (n) => `读取了 ${n} 个文件`,
  code: (n) => `执行了 ${n} 次代码`,
  write: (n) => `撰写了 ${n} 份内容`,
  knowledge: (n) => `查询了 ${n} 次知识库`,
  plan: (n) => `更新了 ${n} 次计划`,
  analysis: (n) => `运行了 ${n} 次分析`,
  file: (n) => `处理了 ${n} 个文件`,
  tool: (n) => `调用了 ${n} 次工具`,
};

const EN_PHRASE: Record<ToolKind, (n: number) => string> = {
  search: (n) => `searched ${n} time${n > 1 ? 's' : ''}`,
  read: (n) => `read ${n} file${n > 1 ? 's' : ''}`,
  code: (n) => `ran code ${n} time${n > 1 ? 's' : ''}`,
  write: (n) => `wrote ${n} item${n > 1 ? 's' : ''}`,
  knowledge: (n) => `queried the knowledge base ${n} time${n > 1 ? 's' : ''}`,
  plan: (n) => `updated the plan ${n} time${n > 1 ? 's' : ''}`,
  analysis: (n) => `ran ${n} analysis${n > 1 ? 'es' : ''}`,
  file: (n) => `handled ${n} file${n > 1 ? 's' : ''}`,
  tool: (n) => `made ${n} tool call${n > 1 ? 's' : ''}`,
};

const KIND_ORDER: ToolKind[] = [
  'search',
  'read',
  'code',
  'write',
  'knowledge',
  'plan',
  'analysis',
  'file',
  'tool',
];

/** Aggregate a group's steps into the "a · b · c" summary line. */
export function summarizeToolGroup<T extends GroupableStep>(
  steps: T[],
  language: 'zh' | 'en',
): string {
  const counts = new Map<ToolKind, number>();
  for (const step of steps) {
    for (const name of toolNamesFromAction(step.action)) {
      const kind = kindForTool(name);
      counts.set(kind, (counts.get(kind) || 0) + 1);
    }
  }
  const phrases = KIND_ORDER.filter((kind) => counts.has(kind)).map((kind) => {
    const n = counts.get(kind)!;
    return (language === 'zh' ? ZH_PHRASE : EN_PHRASE)[kind](n);
  });
  if (phrases.length === 0) {
    return language === 'zh' ? '调用了工具' : 'Used tools';
  }
  return phrases.join(' · ');
}
