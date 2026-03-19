import type { PlanResultItem } from '@/types';

export type VerificationStatus = 'passed' | 'failed' | 'skipped';

export interface VerificationView {
  status: VerificationStatus | null;
  label: string | null;
  color: string;
  checksTotal: number;
  checksPassed: number;
  blocking: boolean;
  generated: boolean;
  failures: Array<Record<string, any>>;
  artifactPaths: string[];
}

export const getVerificationView = (
  result: PlanResultItem | null | undefined
): VerificationView => {
  const verification = result?.metadata?.verification;
  if (!verification || typeof verification !== 'object') {
    return {
      status: null,
      label: null,
      color: 'default',
      checksTotal: 0,
      checksPassed: 0,
      blocking: true,
      generated: false,
      failures: [],
      artifactPaths: [],
    };
  }

  const rawStatus = typeof verification.status === 'string'
    ? verification.status.trim().toLowerCase()
    : '';
  const status: VerificationStatus | null =
    rawStatus === 'passed' || rawStatus === 'failed' || rawStatus === 'skipped'
      ? rawStatus
      : null;

  const label =
    status === 'passed'
      ? 'Verified'
      : status === 'failed'
      ? 'Verification failed'
      : status === 'skipped'
      ? 'Verification skipped'
      : null;

  const color =
    status === 'passed'
      ? 'green'
      : status === 'failed'
      ? 'red'
      : status === 'skipped'
      ? 'default'
      : 'default';

  const failures = Array.isArray(verification.failures)
    ? (verification.failures as Array<Record<string, any>>)
    : [];
  const evidence = verification.evidence;
  const artifactPaths =
    evidence && typeof evidence === 'object' && Array.isArray((evidence as any).artifact_paths)
      ? ((evidence as any).artifact_paths as string[]).filter((item) => typeof item === 'string')
      : [];

  return {
    status,
    label,
    color,
    checksTotal: typeof verification.checks_total === 'number' ? verification.checks_total : 0,
    checksPassed: typeof verification.checks_passed === 'number' ? verification.checks_passed : 0,
    blocking: verification.blocking !== false,
    generated: verification.generated === true,
    failures,
    artifactPaths,
  };
};
