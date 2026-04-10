import { describe, expect, it } from 'vitest';

import { promoteHydratedThinkingVisibility } from './thinkingVisibility';

describe('promoteHydratedThinkingVisibility', () => {
  it('promotes persisted progress visibility to visible when thinking steps exist', () => {
    const metadata: Record<string, any> = { thinking_visibility: 'progress' };

    promoteHydratedThinkingVisibility(metadata, {
      steps: [{ iteration: 1, thought: 'x', status: 'completed' }],
      status: 'completed',
      total_iterations: 1,
    });

    expect(metadata.thinking_visibility).toBe('visible');
  });

  it('keeps progress visibility when there are no persisted steps', () => {
    const metadata: Record<string, any> = { thinking_visibility: 'progress' };

    promoteHydratedThinkingVisibility(metadata, {
      steps: [],
      status: 'completed',
      total_iterations: 0,
    });

    expect(metadata.thinking_visibility).toBe('progress');
  });

  it('does not override hidden visibility', () => {
    const metadata: Record<string, any> = { thinking_visibility: 'hidden' };

    promoteHydratedThinkingVisibility(metadata, {
      steps: [{ iteration: 1, thought: 'x', status: 'completed' }],
      status: 'completed',
      total_iterations: 1,
    });

    expect(metadata.thinking_visibility).toBe('hidden');
  });
});
