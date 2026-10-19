import React from 'react';
import { Tag } from 'antd';
import { useT } from '@/i18n/useT';

type RepairEvent = { metadata?: Record<string, unknown> };

export function RepairProgressBadge({ logs, jobStatus }: { logs: RepairEvent[]; jobStatus: string }) {
  const t = useT();
  const event = [...logs].reverse().find(item => item.metadata?.sub_type === 'repair_progress');
  const repair = event?.metadata;
  if (!repair) return null;
  const attempt = Number(repair.attempt);
  const maximum = Number(repair.max_attempts);
  if (!Number.isInteger(attempt) || !Number.isInteger(maximum) || maximum < 1 || attempt < 0 || attempt > maximum) return null;
  const terminal = ['failed', 'cancelled', 'succeeded', 'completed'].includes(jobStatus);
  const state = String(repair.status || '');
  const active = state === 'repairing' || state === 'checking';
  const label = active && terminal ? t('jobLog.repairEnded')
    : state === 'repairing' ? t('jobLog.repairing')
    : state === 'checking' ? t('jobLog.repairChecking')
    : state === 'completed' ? t('jobLog.repairPassed')
    : state === 'failed' ? t('jobLog.repairFailed') : '';
  if (!label) return null;
  const color = state === 'completed' ? 'success' : active && !terminal ? 'processing' : 'warning';
  return <Tag color={color} data-testid="repair-progress">
    {t('jobLog.repairLine', { id: String(repair.task_id ?? ''), label, attempt, max: maximum })}
  </Tag>;
}
