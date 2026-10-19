import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useLanguageStore } from '@/i18n/language';

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
  beforeEach(() => useLanguageStore.getState().setLanguage('en'));
  afterEach(() => useLanguageStore.getState().setLanguage('zh-CN'));

  it('explains oversized user queries', () => {
    expect(resolveRequestFailureMessage(new Error('User query too long (max 10000 chars)')))
      .toContain('max 10000 characters');
  });

  it('explains LLM quota and rate limit failures', () => {
    expect(resolveRequestFailureMessage(new Error('LLM HTTPError: 429 {"code":"insufficient_quota"}')))
      .toContain('quota or rate limit');
  });

  it('keeps the generic fallback for unknown failures', () => {
    expect(resolveRequestFailureMessage(new Error('unexpected failure')))
      .toContain('Request failed. Please check');
  });

  it('reads in Chinese when the interface is Chinese, for every kind of failure', () => {
    useLanguageStore.getState().setLanguage('zh-CN');
    const cjk = /[\u4e00-\u9fff]/;
    for (const text of ['User query too long (max 10,000 chars)', 'User query too long', '429 insufficient_quota', '401', 'timed out', 'Network Error', '503', 'unexpected failure']) {
      expect(resolveRequestFailureMessage(new Error(text)), text).toMatch(cjk);
    }
    expect(resolveRequestFailureMessage(new Error('User query too long (max 10,000 chars)'))).toContain('最多 10000 个字符');
  });
});
