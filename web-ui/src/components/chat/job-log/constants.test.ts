import { describe, expect, it } from 'vitest';
import { FINAL_STATUSES, normalizeActionStatusKey, statusMeta } from './constants';
import { translate } from '@/i18n/useT';
import { useLanguageStore } from '@/i18n/language';

describe('job-log status vocabulary', () => {
  it('treats every terminal job status as final', () => {
    // 'cancelled' used to live only in the polling hook, so cancelled jobs
    // kept polling forever (2026-10-11).
    expect([...FINAL_STATUSES].sort()).toEqual(['cancelled', 'completed', 'failed', 'succeeded']);
  });

  it('renders cancelled and needs_review badges', () => {
    expect(translate(statusMeta.cancelled.labelKey)).toBe('已取消');
    expect(statusMeta.needs_review.color).toBe('warning');
    expect(translate(statusMeta.needs_review.labelKey)).toBe('待复核');
    useLanguageStore.getState().setLanguage('en');
    try {
      expect(translate(statusMeta.cancelled.labelKey)).toBe('Cancelled');
      expect(translate(statusMeta.needs_review.labelKey)).toBe('Needs review');
    } finally {
      useLanguageStore.getState().setLanguage('zh-CN');
    }
  });

  it('normalizes the new status keys', () => {
    expect(normalizeActionStatusKey('NEEDS_REVIEW')).toBe('needs_review');
    expect(normalizeActionStatusKey('Cancelled')).toBe('cancelled');
  });
});
