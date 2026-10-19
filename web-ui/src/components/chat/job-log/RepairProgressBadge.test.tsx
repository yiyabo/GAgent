import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { RepairProgressBadge } from './RepairProgressBadge';

afterEach(cleanup);
const progress = (status: string, attempt = 1) => ({ metadata: {
  sub_type: 'repair_progress', status, attempt, max_attempts: 2, task_id: 7,
  strategy: attempt === 2 ? 'rebuild_missing_outputs' : 'inspect_and_patch',
} });

describe('delivery repair progress', () => {
  it('shows repair, strategy change, and verified completion from replayed events', () => {
    const { rerender } = render(<RepairProgressBadge logs={[progress('repairing')]} jobStatus="running" />);
    expect(screen.getByTestId('repair-progress').textContent).toContain('正在补齐产物 · 1/2');
    rerender(<RepairProgressBadge logs={[progress('repairing'), progress('checking', 2)]} jobStatus="running" />);
    expect(screen.getByTestId('repair-progress').textContent).toContain('正在重新验收 · 2/2');
    rerender(<RepairProgressBadge logs={[progress('completed', 2)]} jobStatus="succeeded" />);
    expect(screen.getByTestId('repair-progress').textContent).toContain('补交验收通过');
  });
  it('does not leave a cancelled job looking active or carry state into another job', () => {
    const { rerender } = render(<RepairProgressBadge logs={[progress('repairing')]} jobStatus="cancelled" />);
    expect(screen.getByTestId('repair-progress').textContent).toContain('补交已结束');
    rerender(<RepairProgressBadge logs={[]} jobStatus="running" />);
    expect(screen.queryByTestId('repair-progress')).toBeNull();
    rerender(<RepairProgressBadge logs={[progress('failed', 2)]} jobStatus="failed" />);
    expect(screen.getByTestId('repair-progress').textContent).toContain('补交仍未通过 · 2/2');
  });
});

import { useLanguageStore } from '@/i18n/language';
describe('delivery repair progress in English', () => {
  it('reads in English and keeps the task, label and attempt order', () => {
    useLanguageStore.getState().setLanguage('en');
    try {
      const { rerender } = render(<RepairProgressBadge logs={[progress('repairing')]} jobStatus="running" />);
      expect(screen.getByTestId('repair-progress').textContent).toBe('Task #7 · Completing artifacts · 1/2');
      rerender(<RepairProgressBadge logs={[progress('repairing')]} jobStatus="cancelled" />);
      expect(screen.getByTestId('repair-progress').textContent).toContain('Resubmission ended');
    } finally {
      useLanguageStore.getState().setLanguage('zh-CN');
    }
  });
});
