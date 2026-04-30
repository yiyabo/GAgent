import { describe, expect, it } from 'vitest';

import {
  resolveMessageCopyText,
  shouldShowStreamingCursor,
  resolveRequestFailureMessage,
} from './utils';

describe('resolveMessageCopyText', () => {
  it('prefers analysis_text over stale content for assistant messages', () => {
    expect(
      resolveMessageCopyText({
        type: 'assistant',
        content:
          'Request failed. Please check:\n\n1. Backend service availability\n2. LLM API configuration\n3. Network connectivity\n\nThen retry the request.',
        metadata: {
          analysis_text: '## 结构化计划已创建\n\n**Plan ID:** 62',
          final_summary: 'summary fallback',
        },
      } as any),
    ).toBe('## 结构化计划已创建\n\n**Plan ID:** 62');
  });

  it('falls back to assistant content when analysis text is absent', () => {
    expect(
      resolveMessageCopyText({
        type: 'assistant',
        content: 'plain answer',
        metadata: {},
      } as any),
    ).toBe('plain answer');
  });

  it('hides the stream cursor for background-dispatch messages', () => {
    expect(
      shouldShowStreamingCursor({
        unifiedStream: true,
        status: 'running',
        backgroundCategory: 'task_creation',
      }),
    ).toBe(false);
  });

  it('keeps the stream cursor for normal running unified-stream answers', () => {
    expect(
      shouldShowStreamingCursor({
        unifiedStream: true,
        status: 'running',
        backgroundCategory: null,
      }),
    ).toBe(true);
  });
});


describe('resolveRequestFailureMessage', () => {
  it('explains oversized user queries', () => {
    expect(resolveRequestFailureMessage(new Error('User query too long (max 10000 chars)')))
      .toContain('max 10,000 characters');
  });

  it('explains LLM quota and rate limit failures', () => {
    expect(resolveRequestFailureMessage(new Error('LLM HTTPError: 429 {"code":"insufficient_quota"}')))
      .toContain('quota or rate limit');
  });

  it('keeps the generic fallback for unknown failures', () => {
    expect(resolveRequestFailureMessage(new Error('unexpected failure')))
      .toContain('Request failed. Please check');
  });
});
