import { describe, expect, it } from 'vitest';
import type { RunInspection, RunInspectionStep } from '@/types/runInspection';
import {
  exportBlob,
  exportFileName,
  exportJson,
  filterSteps,
  formatDuration,
  formatTokens,
  resolveRunTarget,
  stepTone,
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
    expect(resolveRunTarget({ chat_run_id: 'not-a-run', backend_id: 0 })).toBeNull();
    expect(resolveRunTarget(undefined)).toBeNull();
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
