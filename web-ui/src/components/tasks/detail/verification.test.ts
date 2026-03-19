import { describe, expect, it } from 'vitest';

import type { PlanResultItem } from '@/types';
import { getVerificationView } from './verification';

describe('task verification view', () => {
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
