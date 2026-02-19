import * as React from 'react';
import {
  PauseCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { DecompositionJobStatus, JobLogEvent } from '@/types';

export const MAX_RENDER_LOGS = 200;
export const FINAL_STATUSES = new Set(['succeeded', 'failed', 'completed']);

export const levelColorMap: Record<string, string> = {
  debug: 'default',
  info: 'blue',
  success: 'success',
  warning: 'orange',
  warn: 'orange',
  error: 'red',
  stdout: 'geekblue',
  stderr: 'orange',
};

export const statusMeta: Record<
  string,
  {
    color: string;
    label: string;
    icon: React.ReactNode;
  }
> = {
  queued: {
    color: 'default',
    label: '排队中',
    icon: <PauseCircleOutlined />,
  },
  pending: {
    color: 'default',
    label: '排队中',
    icon: <PauseCircleOutlined />,
  },
  awaiting_confirmation: {
    color: 'default',
    label: '等待确认',
    icon: <PauseCircleOutlined />,
  },
  running: {
    color: 'processing',
    label: '执行中',
    icon: <SyncOutlined spin />,
  },
  succeeded: {
    color: 'success',
    label: '已完成',
    icon: <CheckCircleOutlined />,
  },
  completed: {
    color: 'success',
    label: '已完成',
    icon: <CheckCircleOutlined />,
  },
  failed: {
    color: 'error',
    label: '失败',
    icon: <CloseCircleOutlined />,
  },
};

export const normalizeActionStatusKey = (raw: unknown): string => {
  const key = String(raw ?? '').trim().toLowerCase();
  if (!key) return 'queued';
  if (key === 'completed') return 'completed';
  if (key === 'succeeded') return 'succeeded';
  if (key === 'failed') return 'failed';
  if (key === 'running') return 'running';
  if (key === 'pending') return 'pending';
  if (key === 'awaiting_confirmation') return 'awaiting_confirmation';
  return key;
};

export const parseIsoToMs = (value?: string | null): number | null => {
  if (!value) return null;
  const ts = Date.parse(value);
  return Number.isNaN(ts) ? null : ts;
};

export const jobTypeMeta: Record<
  string,
  {
    label: string;
    color: string;
  }
> = {
  plan_decompose: {
    label: '任务拆分日志',
    color: 'blue',
  },
  plan_execute: {
    label: '计划执行日志',
    color: 'green',
  },
  chat_action: {
    label: '动作执行日志',
    color: 'purple',
  },
  default: {
    label: '后台任务日志',
    color: 'geekblue',
  },
};

export const toNumber = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

export type StreamMessage =
  | { type: 'snapshot'; job: DecompositionJobStatus }
  | { type: 'heartbeat'; job: Partial<DecompositionJobStatus> & { job_id: string; status: string } }
  | {
      type: 'event';
      job_id: string;
      status?: string;
      event?: JobLogEvent;
      result?: Record<string, any>;
      error?: string | null;
      stats?: Record<string, any>;
      job_type?: string | null;
      metadata?: Record<string, any>;
    };

export const parseStreamData = (raw: MessageEvent<any>): StreamMessage | null => {
  try {
    const payload = JSON.parse(raw.data);
    if (!payload) return null;
    if (payload.type === 'snapshot') {
      return payload as StreamMessage;
    }
    if (payload.type === 'heartbeat') {
      return payload as StreamMessage;
    }
    payload.type = 'event';
    return payload as StreamMessage;
  } catch (error) {
    console.warn('无法解析 SSE 消息:', error);
    return null;
  }
};

export interface JobLogPanelProps {
  jobId: string;
  initialJob?: DecompositionJobStatus | null;
  targetTaskName?: string | null;
  planId?: number | null;
  jobType?: string | null;
}
