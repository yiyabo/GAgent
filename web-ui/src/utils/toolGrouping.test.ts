import { describe, expect, it } from 'vitest';

import {
  buildStreamRows,
  kindForTool,
  summarizeToolGroup,
  toolNamesFromAction,
  type GroupableStep,
} from './toolGrouping';

const step = (iteration: number, action?: string | null): GroupableStep => ({
  iteration,
  action: action ?? null,
});

const toolAction = (tool: string, params: Record<string, unknown> = {}) =>
  JSON.stringify({ tool, params });

describe('toolNamesFromAction', () => {
  it('parses single-tool actions', () => {
    expect(toolNamesFromAction(toolAction('web_search', { query: 'x' }))).toEqual(['web_search']);
  });

  it('parses multi-tool arrays and counts each tool', () => {
    const action = JSON.stringify({ tools: [{ tool: 'web_search' }, { tool: 'file_operations' }] });
    expect(toolNamesFromAction(action)).toEqual(['web_search', 'file_operations']);
  });

  it('falls back to unknown for unparseable payloads', () => {
    expect(toolNamesFromAction('not json')).toEqual(['unknown']);
    expect(toolNamesFromAction(undefined)).toEqual([]);
  });
});

describe('kindForTool', () => {
  it('maps known tools and defaults the rest', () => {
    expect(kindForTool('web_search')).toBe('search');
    expect(kindForTool('execute_code')).toBe('code');
    expect(kindForTool('lightrag_query')).toBe('knowledge');
    expect(kindForTool('some_new_tool')).toBe('tool');
  });
});

describe('buildStreamRows', () => {
  it('folds runs of 2+ consecutive tool steps into one group', () => {
    const steps = [
      step(1),
      step(2, toolAction('web_search')),
      step(3, toolAction('document_reader')),
      step(4),
    ];
    const rows = buildStreamRows(steps);
    expect(rows.map((r) => r.type)).toEqual(['step', 'tool_group', 'step']);
    const group = rows[1] as Extract<(typeof rows)[number], { type: 'tool_group' }>;
    expect(group.group.steps.map((s) => s.iteration)).toEqual([2, 3]);
    expect(group.group.id).toBe('grp-2-3');
  });

  it('keeps single tool steps solo', () => {
    const steps = [step(1), step(2, toolAction('web_search')), step(3)];
    const rows = buildStreamRows(steps);
    expect(rows.every((r) => r.type === 'step')).toBe(true);
  });

  it('never folds the last step when keepLastSolo (live visibility)', () => {
    const steps = [
      step(1, toolAction('web_search')),
      step(2, toolAction('document_reader')),
      step(3, toolAction('code_executor')),
    ];
    const rows = buildStreamRows(steps, { keepLastSolo: true });
    // steps 1+2 fold into one group; the live step 3 stays solo
    expect(rows.map((r) => r.type)).toEqual(['tool_group', 'step']);
    const group = rows[0] as Extract<(typeof rows)[number], { type: 'tool_group' }>;
    expect(group.group.steps.map((s) => s.iteration)).toEqual([1, 2]);
    const last = rows[rows.length - 1] as Extract<(typeof rows)[number], { type: 'step' }>;
    expect(last.step.iteration).toBe(3);
  });

  it('does not fold non-adjacent tool steps', () => {
    const steps = [
      step(1, toolAction('web_search')),
      step(2),
      step(3, toolAction('web_search')),
    ];
    const rows = buildStreamRows(steps);
    expect(rows.map((r) => r.type)).toEqual(['step', 'step', 'step']);
  });
});

describe('summarizeToolGroup', () => {
  it('aggregates kinds in canonical order (zh)', () => {
    const steps = [
      step(1, toolAction('web_search')),
      step(2, toolAction('document_reader')),
      step(3, toolAction('document_reader')),
      step(4, toolAction('execute_code')),
    ];
    expect(summarizeToolGroup(steps, 'zh')).toBe('检索了 1 次资料 · 读取了 2 个文件 · 执行了 1 次代码');
  });

  it('counts multi-tool steps per tool (en)', () => {
    const steps = [
      step(1, JSON.stringify({ tools: [{ tool: 'web_search' }, { tool: 'lightrag_query' }] })),
      step(2, toolAction('web_search')),
    ];
    expect(summarizeToolGroup(steps, 'en')).toBe(
      'searched 2 times · queried the knowledge base 1 time',
    );
  });

  it('falls back to generic label when nothing parses', () => {
    expect(summarizeToolGroup([step(1, 'garbage')], 'zh')).toBe('调用了 1 次工具');
  });
});
