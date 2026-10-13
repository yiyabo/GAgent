import React from 'react';
import { Tag } from 'antd';

type RepairEvent = { metadata?: Record<string, unknown> };

export function RepairProgressBadge({ logs, jobStatus }: { logs: RepairEvent[]; jobStatus: string }) {
  const event = [...logs].reverse().find(item => item.metadata?.sub_type === 'repair_progress');
  const repair = event?.metadata;
  if (!repair) return null;
  const attempt = Number(repair.attempt);
  const maximum = Number(repair.max_attempts);
  if (!Number.isInteger(attempt) || !Number.isInteger(maximum) || maximum < 1 || attempt < 0 || attempt > maximum) return null;
  const terminal = ['failed', 'cancelled', 'succeeded', 'completed'].includes(jobStatus);
  const state = String(repair.status || '');
  const active = state === 'repairing' || state === 'checking';
  const label = active && terminal ? '补交已结束，记录待核对'
    : state === 'repairing' ? '正在补齐产物'
    : state === 'checking' ? '正在重新验收'
    : state === 'completed' ? '补交验收通过'
    : state === 'failed' ? '补交仍未通过' : '';
  if (!label) return null;
  const color = state === 'completed' ? 'success' : active && !terminal ? 'processing' : 'warning';
  return <Tag color={color} data-testid="repair-progress">
    任务 #{String(repair.task_id ?? '')} · {label} · {attempt}/{maximum}
  </Tag>;
}
