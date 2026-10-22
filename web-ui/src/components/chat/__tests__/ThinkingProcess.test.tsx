import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useLanguageStore } from '@/i18n/language';
import type { ThinkingProcess as ThinkingProcessData, ThinkingStep } from '@/types';

vi.mock('../MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div data-testid="md">{content}</div>,
}));

import { ThinkingProcess } from '../ThinkingProcess';

const TOOL = '{"tool":"bio_tools","params":{"operation":"stats"}}';

const step = (iteration: number, extra: Partial<ThinkingStep> = {}): ThinkingStep => ({
  iteration,
  thought: '',
  display_text: 'Working through the current step',
  kind: 'reasoning',
  status: 'thinking',
  ...extra,
});

const process = (steps: ThinkingStep[], status: ThinkingProcessData['status'] = 'active'): ThinkingProcessData => ({
  steps,
  status,
  total_iterations: steps.length,
});

const labels = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('.tp-item-label')).map((el) => el.textContent);

beforeEach(() => {
  useLanguageStore.getState().setLanguage('en');
  // jsdom has no layout, so it does not implement scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  useLanguageStore.getState().setLanguage('zh-CN');
});

describe('ThinkingProcess rows', () => {
  it('opens the row being worked on before any text has arrived', () => {
    const { container } = render(
      <ThinkingProcess
        process={process([
          step(1, { kind: 'tool', status: 'done', action: TOOL, action_result: 'ok' }),
          step(2),
        ])}
      />,
    );
    const live = container.querySelector('.tp-item-row.active.expandable') as HTMLElement;
    expect(live).not.toBeNull();
    expect(screen.queryByText('Thinking…')).toBeNull();

    fireEvent.click(live);
    expect(screen.getByText('Thinking…')).toBeTruthy();
  });

  it('shows the reasoning and the thought on the tool call that followed them', () => {
    const { container } = render(
      <ThinkingProcess
        process={process([
          step(1, {
            kind: 'tool',
            status: 'done',
            action: TOOL,
            action_result: 'ok',
            reasoning: 'The file is a BAM, so stats is the right call.',
            thought: 'Running stats now.',
          }),
          step(2),
        ])}
      />,
    );
    const toolRow = container.querySelector('.tp-item-row.expandable:not(.active)') as HTMLElement;
    expect(screen.queryByText('Thinking')).toBeNull();

    fireEvent.click(toolRow);
    expect(screen.getByText('Thinking')).toBeTruthy();
    const text = screen.getAllByTestId('md').map((el) => el.textContent);
    expect(text).toEqual(['The file is a BAM, so stats is the right call.', 'Running stats now.']);
  });

  it('keeps a tool call without any thinking text free of the Thinking section', () => {
    const { container } = render(
      <ThinkingProcess
        process={process([
          step(1, { kind: 'tool', status: 'done', action: TOOL, action_result: 'ok' }),
          step(2),
        ])}
      />,
    );
    fireEvent.click(container.querySelector('.tp-item-row.expandable:not(.active)') as HTMLElement);
    expect(screen.queryByText('Thinking')).toBeNull();
  });

  it('spins only the step that is still running', () => {
    const { container } = render(
      <ThinkingProcess
        process={process([
          step(1, { status: 'done', reasoning: 'earlier reasoning' }),
          step(2, { status: 'thinking' }),
        ])}
      />,
    );
    expect(labels(container).filter((label) => label === 'Thought process')).toHaveLength(2);
    expect(container.querySelectorAll('.tp-item-row.active')).toHaveLength(1);
  });

  it('keeps the final reasoning of a finished run after its thought was cleared', () => {
    const { container } = render(
      <ThinkingProcess
        isFinished
        process={process(
          [
            step(1, { kind: 'tool', status: 'done', action: TOOL, action_result: 'ok' }),
            step(2, { status: 'done', thought: '', reasoning: 'Why the answer is 42.' }),
          ],
          'completed',
        )}
      />,
    );
    fireEvent.click(container.querySelector('.tp-header') as HTMLElement);
    const rows = Array.from(container.querySelectorAll('.tp-item-row.expandable'));
    expect(rows.length).toBeGreaterThan(0);
    fireEvent.click(rows[rows.length - 1] as HTMLElement);
    expect(screen.getByText('Why the answer is 42.')).toBeTruthy();
  });
});
