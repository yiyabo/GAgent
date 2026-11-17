import { describe, expect, it } from 'vitest';
import i18n from '@/i18n';
import type { TFn } from '@/i18n/useT';
import type { RunInspection, RunInspectionStep } from '@/types/runInspection';
import {
  exportBlob,
  exportFileName,
  exportJson,
  filterSteps,
  formatDuration,
  formatStepCounts,
  formatTokens,
  isPlanTaskTarget,
  resolveRunTarget,
  stepTone,
  taskHasRun,
} from './model';

const step = (overrides: Partial<RunInspectionStep>): RunInspectionStep => ({
  first_seq: 1,
  last_seq: 2,
  iteration: 1,
  kind: 'reasoning',
  status: 'done',
  tools: [],
  ...overrides,
});

describe('run inspector model', () => {
  it('resolves the live run id before falling back to the persisted message id', () => {
    expect(resolveRunTarget({ chat_run_id: 'dt_live', backend_id: 9 })).toEqual({ run_id: 'dt_live' });
    expect(resolveRunTarget({ deep_think_job_id: 'dt_job' })).toEqual({ run_id: 'dt_job' });
    expect(resolveRunTarget({ backend_id: 42 })).toEqual({ message_id: 42 });
    expect(resolveRunTarget({ chat_run_id: '  ', backend_id: 0 })).toBeNull();
    expect(resolveRunTarget(undefined)).toBeNull();
  });

  it('accepts any persisted run id or message id a chat message carries', () => {
    expect(resolveRunTarget({ run_id: 'run-42' })).toEqual({ run_id: 'run-42' });
    expect(resolveRunTarget({ chat_run_id: ' run-A ' })).toEqual({ run_id: 'run-A' });
    expect(resolveRunTarget({ chat_run_id: 7, backend_id: '17' })).toEqual({ message_id: 17 });
    expect(resolveRunTarget({ message_id: 5 })).toEqual({ message_id: 5 });
    expect(resolveRunTarget({ backend_id: 1.5 })).toBeNull();
    expect(resolveRunTarget({ backend_id: '-3' })).toBeNull();
    expect(resolveRunTarget({})).toBeNull();
  });

  it('tells plan-task targets from chat targets', () => {
    expect(isPlanTaskTarget({ planId: 3, taskId: 7 })).toBe(true);
    expect(isPlanTaskTarget({ run_id: 'dt_x' })).toBe(false);
    expect(isPlanTaskTarget({ message_id: 4 })).toBe(false);
    expect(isPlanTaskTarget(null)).toBe(false);
  });

  it('offers inspection only for tasks that have run', () => {
    expect(taskHasRun({ status: 'pending' })).toBe(false);
    expect(taskHasRun({})).toBe(false);
    expect(taskHasRun({ status: 'completed' })).toBe(true);
    expect(taskHasRun({ status: 'failed' })).toBe(true);
    expect(taskHasRun({ status: 'running' })).toBe(true);
    expect(taskHasRun({ status: 'pending', execution_result: 'partial output' })).toBe(true);
    expect(taskHasRun({ status: 'pending', execution_result: '' })).toBe(false);
  });

  it('pluralises the step counts per count in English', () => {
    const en = i18n.getFixedT('en') as unknown as TFn;
    expect(formatStepCounts(en, 1, 1)).toBe('1 step · 1 tool step');
    expect(formatStepCounts(en, 3, 2)).toBe('3 steps · 2 tool steps');
    expect(formatStepCounts(en, 0, 0)).toBe('0 steps · 0 tool steps');
    expect(formatStepCounts(en, 2, 1)).toBe('2 steps · 1 tool step');
    const zh = i18n.getFixedT('zh-CN') as unknown as TFn;
    expect(formatStepCounts(zh, 1, 1)).toBe('1 步 · 其中工具 1 步');
    expect(formatStepCounts(zh, 3, 2)).toBe('3 步 · 其中工具 2 步');
  });

  it('filters by kind, failure and tool name', () => {
    const steps = [
      step({ first_seq: 1 }),
      step({ first_seq: 2, kind: 'tool', tools: [{ tool: 'web_search' }], success: true }),
      step({ first_seq: 3, kind: 'tool', tools: [{ tool: 'execute_code' }], success: false }),
    ];
    expect(filterSteps(steps, 'all', null)).toHaveLength(3);
    expect(filterSteps(steps, 'tools', null).map((s) => s.first_seq)).toEqual([2, 3]);
    expect(filterSteps(steps, 'failures', null).map((s) => s.first_seq)).toEqual([3]);
    expect(filterSteps(steps, 'all', 'web_search').map((s) => s.first_seq)).toEqual([2]);
  });

  it('formats durations, tokens and step tone', () => {
    expect(formatDuration(null)).toBe('—');
    expect(formatDuration(450)).toBe('450 ms');
    expect(formatDuration(4300)).toBe('4.3 s');
    expect(formatDuration(1_093_000)).toBe('18m 13s');
    expect(formatTokens(4_236_223)).toBe('4.24M');
    expect(formatTokens(12_500)).toBe('12.5k');
    expect(formatTokens(130)).toBe('130');
    expect(stepTone(step({ success: false }))).toBe('error');
    expect(stepTone(step({ success: true }))).toBe('success');
    expect(stepTone(step({ status: 'calling_tool', finished_at: null }))).toBe('processing');
  });

  it('exports the inspected payload verbatim', () => {
    const inspection = { version: 1, run: { run_id: 'dt_x' } } as unknown as RunInspection;
    expect(exportFileName(inspection)).toBe('run-dt_x.json');
    expect(JSON.parse(exportJson(inspection))).toEqual(inspection);
    const blob = exportBlob(inspection);
    expect(blob.type).toBe('application/json');
    expect(blob.size).toBe(new TextEncoder().encode(exportJson(inspection)).length);
  });
});
