import { describe, expect, it } from 'vitest';
import {
  collectToolResultsFromActions,
  collectToolResultsFromMetadata,
  collectToolResultsFromSteps,
  mergeToolResults,
} from './toolResults';

describe('toolResults utilities', () => {
  it('collects tool results from steps', () => {
    const steps = [
      {
        action: { kind: 'plan_operation', name: 'create_plan' },
        details: {},
      },
      {
        action: {
          kind: 'tool_operation',
          name: 'web_search',
          parameters: { query: 'latest ai news' },
        },
        success: true,
        details: {
          summary: 'Search complete',
          result: {
            query: 'latest ai news',
            success: true,
            response: 'Top highlights...',
            results: [
              { title: 'AI Weekly', url: 'https://example.com', source: 'Example', snippet: '...' },
            ],
          },
        },
      },
    ];
    const payloads = collectToolResultsFromSteps(steps);
    expect(payloads).toHaveLength(1);
    expect(payloads[0].name).toBe('web_search');
    expect(payloads[0].result?.query).toBe('latest ai news');
    expect(payloads[0].result?.results).toHaveLength(1);
  });

  it('merges tool results without duplicates', () => {
    const existing = collectToolResultsFromMetadata([
      {
        name: 'web_search',
        summary: 'Search complete',
        result: { query: 'q1', success: true },
      },
    ]);
    const additional = collectToolResultsFromMetadata([
      {
        name: 'web_search',
        summary: 'Search complete',
        result: { query: 'q1', success: true },
      },
      {
        name: 'web_search',
        summary: 'Second search',
        result: { query: 'q2', success: false, error: 'timeout' },
      },
    ]);

    const merged = mergeToolResults(existing, additional);
    expect(merged).toHaveLength(2);
    expect(merged[0].result?.query).toBe('q1');
    expect(merged[1].result?.query).toBe('q2');
  });

  it('collects from action payloads', () => {
    const actions = [
      {
        kind: 'tool_operation',
        name: 'web_search',
        parameters: { query: 'abc' },
        details: { result: { query: 'abc', success: true } },
      },
    ];
    const payloads = collectToolResultsFromActions(actions as any);
    expect(payloads).toHaveLength(1);
    expect(payloads[0].result?.query).toBe('abc');
  });
});
