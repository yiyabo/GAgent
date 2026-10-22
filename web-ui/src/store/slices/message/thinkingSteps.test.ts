import { describe, expect, it } from 'vitest';
import { closeSupersededSteps } from './thinkingSteps';

const step = (iteration: number, extra: Record<string, unknown> = {}) => ({
  iteration,
  status: 'thinking' as const,
  ...extra,
});

describe('closeSupersededSteps', () => {
  it('closes a reasoning step once a later iteration exists', () => {
    const steps = [
      step(1, { started_at: '2026-10-22T01:00:00' }),
      step(2, { started_at: '2026-10-22T01:00:47' }),
    ];
    const out = closeSupersededSteps(steps);
    expect(out[0]).toMatchObject({ status: 'done', finished_at: '2026-10-22T01:00:47' });
    expect(out[1].status).toBe('thinking');
  });

  it('leaves the newest iteration alone, however many steps came before', () => {
    const out = closeSupersededSteps([step(1), step(2), step(3)]);
    expect(out.map((s) => s.status)).toEqual(['done', 'done', 'thinking']);
  });

  it('closes a finished tool step that is still marked analyzing', () => {
    const out = closeSupersededSteps([
      step(1, { status: 'analyzing', action: '{"tool":"bio_tools"}', action_result: 'ok' }),
      step(2),
    ]);
    expect(out[0].status).toBe('done');
  });

  it('keeps a tool step without a result open: it may still be running', () => {
    const running = step(1, { status: 'calling_tool', action: '{"tool":"bio_tools"}' });
    const out = closeSupersededSteps([running, step(2)]);
    expect(out[0]).toBe(running);
  });

  it('keeps a finish time the step already has', () => {
    const out = closeSupersededSteps([
      step(1, { finished_at: '2026-10-22T01:00:10' }),
      step(2, { started_at: '2026-10-22T01:00:47' }),
    ]);
    expect(out[0].finished_at).toBe('2026-10-22T01:00:10');
  });

  it('does not touch steps that already ended or the iteration-0 bucket', () => {
    const done = step(1, { status: 'done' });
    const errored = step(2, { status: 'error' });
    const bucket = step(0);
    const out = closeSupersededSteps([bucket, done, errored, step(3)]);
    expect(out[0]).toBe(bucket);
    expect(out[1]).toBe(done);
    expect(out[2]).toBe(errored);
  });

  it('returns the same array when nothing changes', () => {
    const steps = [step(1)];
    expect(closeSupersededSteps(steps)).toBe(steps);
    expect(closeSupersededSteps([])).toEqual([]);
  });
});
