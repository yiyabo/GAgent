import { describe, expect, it } from 'vitest';

import { hydratePersistedMessage } from './historyHydration';

describe('hydratePersistedMessage', () => {
  it('normalizes persisted thinking payloads for history hydration', () => {
    const hydrated = hydratePersistedMessage({
      sessionId: 'session-1',
      index: 0,
      rawMessage: {
        id: 9,
        role: 'assistant',
        content: 'final answer',
        timestamp: '2026-04-09T00:00:00Z',
        metadata: {
          thinking_visibility: 'progress',
          thinking_process: JSON.stringify({
            status: 'done',
            total_iterations: 1,
            steps: [
              {
                iteration: '1',
                thought: 'inspect files',
                status: 'done',
              },
            ],
          }),
        },
      },
    });

    expect(hydrated.id).toBe('session-1_9');
    expect(hydrated.thinking_process?.status).toBe('completed');
    expect(hydrated.thinking_process?.steps[0]?.iteration).toBe(1);
    expect((hydrated.metadata as any).thinking_visibility).toBe('visible');
    expect((hydrated.metadata as any).thinking_display_mode).toBe('final_answer');
  });

  it('reuses live tool results when persisted metadata has not caught up yet', () => {
    const fallbackToolResults = new Map([
      [
        'act_1',
        [
          {
            name: 'document_reader',
            summary: 'Read the generated report',
            result: { success: true },
          },
        ],
      ],
    ]);

    const hydrated = hydratePersistedMessage({
      sessionId: 'session-1',
      index: 0,
      fallbackToolResults,
      rawMessage: {
        id: 10,
        role: 'assistant',
        content: 'report ready',
        timestamp: '2026-04-09T00:00:01Z',
        metadata: {
          tracking_id: 'act_1',
        },
      },
    });

    expect((hydrated.metadata as any).tool_results).toEqual(
      fallbackToolResults.get('act_1'),
    );
  });
});
