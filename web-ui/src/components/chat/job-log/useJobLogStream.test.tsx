import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { planTreeApi } from '@api/planTree';
import type { DecompositionJobStatus } from '@/types';
import { useJobLogStream } from './useJobLogStream';

vi.mock('@api/planTree', () => ({ planTreeApi: { getJobStatus: vi.fn(), controlJob: vi.fn(), getJobLogTail: vi.fn() } }));
vi.mock('@utils/planSyncEvents', () => ({ dispatchPlanSyncEvent: vi.fn() }));
const api = vi.mocked(planTreeApi);
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent); }
}
const snapshot = (jobId = 'job-a', status = 'running'): DecompositionJobStatus => ({
  job_id: jobId, plan_id: 1, job_type: 'plan_execute', status,
  metadata: { plan_id: 1, message_preview: '研究任务' }, stats: { completed: 0 },
  logs: [], result: { content: '执行中' },
});
const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  api.getJobStatus.mockImplementation(async (id) => {
    // Bound a failing regression so a dependency loop reports failure instead of hanging Vitest.
    if (api.getJobStatus.mock.calls.length > 12) return new Promise(() => {});
    return snapshot(id);
  });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('job stream connection lifecycle', () => {
  it('keeps one bootstrap and stream despite fresh metadata and changing heartbeat content', async () => {
    const {result} = renderHook(() => useJobLogStream({jobId: 'job-a', planId: 1}));
    await settle();
    expect(api.getJobStatus).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    for (let index = 0; index < 10; index++) {
      await act(async () => source.emit({type: 'snapshot', job: {
        ...snapshot(), result: {content: `进展 ${index}`}, metadata: {plan_id: 1, message_preview: `步骤 ${index}`},
      }}));
      await act(async () => source.emit({type: 'heartbeat', job: snapshot()}));
    }
    expect(result.current.status).toBe('running');
    expect(api.getJobStatus).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(source.close).not.toHaveBeenCalled();
  });

  it('uses bounded fallback polling and backs off after thirty seconds', async () => {
    renderHook(() => useJobLogStream({jobId: 'job-a', planId: 1}));
    await settle();
    await act(async () => { FakeEventSource.instances[0].onerror?.(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4999); });
    expect(api.getJobStatus).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(api.getJobStatus).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(55_000); });
    expect(api.getJobStatus).toHaveBeenCalledTimes(9);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('does not apply or reschedule an in-flight poll after switching jobs', async () => {
    let finishOld!: (value: DecompositionJobStatus) => void;
    let oldRequests = 0;
    api.getJobStatus.mockImplementation((id) => {
      if (id === 'job-a' && ++oldRequests > 1) return new Promise((resolve) => { finishOld = resolve; });
      return Promise.resolve(snapshot(id));
    });
    const {result, rerender} = renderHook(({jobId}) => useJobLogStream({jobId, planId: 1}), {initialProps: {jobId: 'job-a'}});
    await settle();
    const oldSource = FakeEventSource.instances[0];
    await act(async () => { oldSource.onerror?.(); await vi.advanceTimersByTimeAsync(5000); });
    rerender({jobId: 'job-b'});
    await settle();
    await act(async () => { finishOld(snapshot('job-a', 'failed')); });
    await act(async () => { oldSource.emit({type: 'snapshot', job: snapshot('job-a', 'failed')}); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(result.current.status).toBe('running');
    expect(api.getJobStatus.mock.calls.map(([id]) => id)).toEqual(['job-a', 'job-a', 'job-b']);
  });

  it('does not open a stream for a finished initial snapshot', async () => {
    api.getJobStatus.mockResolvedValue(snapshot('job-a', 'succeeded'));
    const {result} = renderHook(() => useJobLogStream({jobId: 'job-a', planId: 1}));
    await settle();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(result.current.status).toBe('succeeded');
    expect(api.getJobStatus).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it('stops fallback polling after terminal status', async () => {
    api.getJobStatus.mockResolvedValueOnce(snapshot()).mockResolvedValue(snapshot('job-a', 'succeeded'));
    renderHook(() => useJobLogStream({jobId: 'job-a', planId: 1}));
    await settle();
    await act(async () => { FakeEventSource.instances[0].onerror?.(); await vi.advanceTimersByTimeAsync(60_000); });
    expect(api.getJobStatus).toHaveBeenCalledTimes(2);
  });
});
