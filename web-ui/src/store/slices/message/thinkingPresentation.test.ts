import { describe, expect, it } from 'vitest';

import {
  normalizeHydratedThinkingPresentation,
  resolveThinkingDisplayMode,
} from './thinkingPresentation';

describe('resolveThinkingDisplayMode', () => {
  it('prefers compact progress while a progress-mode run is still active', () => {
    expect(
      resolveThinkingDisplayMode({
        metadata: {
          status: 'running',
          thinking_visibility: 'progress',
          deep_think_progress: { phase: 'gathering' },
        },
        thinkingProcess: undefined,
        isStreaming: true,
      }),
    ).toBe('compact_progress');
  });

  it('promotes active thinking steps to full thinking even if progress metadata exists', () => {
    expect(
      resolveThinkingDisplayMode({
        metadata: {
          status: 'running',
          thinking_visibility: 'progress',
          deep_think_progress: { phase: 'gathering' },
        },
        thinkingProcess: {
          steps: [{ iteration: 1, thought: 'x', status: 'thinking' }],
          status: 'active',
          total_iterations: 1,
        },
        isStreaming: true,
      }),
    ).toBe('full_thinking');
  });

  it('demotes completed thinking into final-answer mode', () => {
    expect(
      resolveThinkingDisplayMode({
        metadata: {
          status: 'completed',
          thinking_visibility: 'progress',
          deep_think_progress: { phase: 'finalizing' },
        },
        thinkingProcess: {
          steps: [{ iteration: 1, thought: 'x', status: 'completed' }],
          status: 'completed',
          total_iterations: 1,
        },
        isStreaming: false,
      }),
    ).toBe('final_answer');
  });
});

describe('normalizeHydratedThinkingPresentation', () => {
  it('promotes persisted progress-mode messages with steps into final-answer presentation', () => {
    const metadata: Record<string, any> = {
      thinking_visibility: 'progress',
      deep_think_progress: { phase: 'finalizing' },
      status: 'completed',
    };

    normalizeHydratedThinkingPresentation(metadata, {
      steps: [{ iteration: 1, thought: 'x', status: 'completed' }],
      status: 'completed',
      total_iterations: 1,
    });

    expect(metadata.thinking_visibility).toBe('visible');
    expect(metadata.thinking_display_mode).toBe('final_answer');
  });
});
