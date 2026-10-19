import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useLanguageStore } from '@/i18n/language';

import type { PlanResultItem } from '@/types';
import { getVerificationView } from './verification';

describe('task verification view', () => {
  beforeEach(() => useLanguageStore.setState({ language: 'en' }));
  afterEach(() => useLanguageStore.setState({ language: 'zh-CN' }));

  it('keeps rule errors separate from failed checks', () => {
    useLanguageStore.setState({ language: 'zh-CN' });
    const view = getVerificationView({ task_id: 1, status: 'needs_review', metadata: {
      verification: { status: 'config_error', failures: [], config_errors: [
        { type: 'glob_count_at_least', message: 'This check counts files, not records.' },
      ] },
    } });
    expect(view.status).toBe('config_error');
    expect(view.label).toBe('验收规则需修正');
    expect(view.color).toBe('gold');
    expect(view.failures).toEqual([]);
    expect(view.configErrors).toHaveLength(1);
  });
  it('returns null status when verification metadata is missing', () => {
    const view = getVerificationView({ task_id: 1 } as PlanResultItem);
    expect(view.status).toBeNull();
    expect(view.label).toBeNull();
  });

  it('maps passed verification metadata', () => {
    const view = getVerificationView({
      task_id: 1,
      metadata: {
        verification: {
          status: 'passed',
          checks_total: 3,
          checks_passed: 3,
          failures: [],
          evidence: { artifact_paths: ['/tmp/out.txt'] },
        },
      },
    });
    expect(view.status).toBe('passed');
    expect(view.label).toBe('Verified');
    expect(view.checksTotal).toBe(3);
    expect(view.artifactPaths).toEqual(['/tmp/out.txt']);
  });

  it('maps failed verification metadata and keeps failures', () => {
    const view = getVerificationView({
      task_id: 2,
      metadata: {
        verification: {
          status: 'failed',
          checks_total: 2,
          checks_passed: 1,
          blocking: true,
          generated: true,
          failures: [{ type: 'file_exists', message: 'missing' }],
          evidence: { artifact_paths: ['/tmp/missing.txt'] },
        },
      },
    });
    expect(view.status).toBe('failed');
    expect(view.label).toBe('Verification failed');
    expect(view.generated).toBe(true);
    expect(view.failures).toHaveLength(1);
  });
});
