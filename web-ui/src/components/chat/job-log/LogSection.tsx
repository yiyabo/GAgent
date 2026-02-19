import * as React from 'react';
import { Space, Tag, Typography, Alert, Divider } from 'antd';
import dayjs from 'dayjs';
import type { ActionLogEntry, JobLogEvent } from '@/types';
import { levelColorMap, statusMeta, normalizeActionStatusKey, parseIsoToMs } from './constants';

const { Text } = Typography;

export const LogMetadata: React.FC<{ metadata: Record<string, any> | undefined }> = ({ metadata }) => {
  if (!metadata || Object.keys(metadata).length === 0) return null;
  const pretty = JSON.stringify(metadata, null, 2);
  return (
    <div
      style={{
        background: '#f7f7f7',
        padding: 8,
        borderRadius: 4,
        marginTop: 4,
        maxHeight: 180,
        overflow: 'auto',
      }}
    >
      <pre
        style={{
          fontSize: 12,
          margin: 0,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {pretty}
      </pre>
    </div>
  );
};

export const ActionLogs: React.FC<{ actionLogs: ActionLogEntry[] }> = ({ actionLogs }) => {
  if (!actionLogs.length) {
    return null;
  }

  // UX: coalesce the common pattern:
  //   [running "Action execution started."] -> [completed/failed "... succeeded/failed ..."]
  // into a single row showing the final status + duration, to avoid the illusion of being stuck.
  const condensed = (() => {
    const sorted = [...actionLogs].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));
    const merged: ActionLogEntry[] = [];
    for (const entry of sorted) {
      const prev = merged.length ? merged[merged.length - 1] : null;
      if (!prev) {
        merged.push(entry);
        continue;
      }
      const prevKey = normalizeActionStatusKey(prev.status);
      const curKey = normalizeActionStatusKey(entry.status);
      const sameAction =
        prev.action_kind === entry.action_kind &&
        prev.action_name === entry.action_name;
      const looksLikeStart =
        prevKey === 'running' &&
        typeof prev.message === 'string' &&
        prev.message.toLowerCase().includes('action execution started');
      const isFinal = curKey === 'completed' || curKey === 'succeeded' || curKey === 'failed';

      if (sameAction && looksLikeStart && isFinal) {
        const startMs = parseIsoToMs(prev.created_at ?? prev.updated_at);
        const endMs = parseIsoToMs(entry.created_at ?? entry.updated_at);
        const durationMs =
          startMs !== null && endMs !== null && endMs >= startMs ? endMs - startMs : null;
        const mergedEntry: ActionLogEntry = {
          ...entry,
          // preserve the original start details for debugging
          details: {
            ...(entry.details ?? {}),
            _start: {
              sequence: prev.sequence,
              status: prev.status,
              message: prev.message,
              created_at: prev.created_at,
              updated_at: prev.updated_at,
            },
            ...(durationMs !== null ? { duration_ms: durationMs } : {}),
          },
        };
        // Replace the previous row with the merged final row
        merged[merged.length - 1] = mergedEntry;
        continue;
      }
      merged.push(entry);
    }
    return merged;
  })();

  return (
    <div style={{ width: '100%' }}>
      <Divider plain style={{ margin: '12px 0' }}>
        动作执行记录
      </Divider>
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        {condensed.map((entry) => {
          const statusKey = normalizeActionStatusKey(entry.status || '');
          const statusInfo = statusMeta[statusKey] || statusMeta.queued;
          const descriptor = entry.action_name ? `${entry.action_kind}/${entry.action_name}` : entry.action_kind;
          const timestamp = entry.created_at ?? entry.updated_at;
          const durationMs =
            entry.details && typeof (entry.details as any).duration_ms === 'number'
              ? ((entry.details as any).duration_ms as number)
              : null;
          return (
            <div key={`action_${entry.sequence}`} style={{ fontSize: 12, borderLeft: '2px solid #f0f0f0', paddingLeft: 8 }}>
              <Space size="small">
                <Tag color={statusInfo.color} style={{ marginRight: 0 }}>
                  {statusInfo.icon}
                  <span style={{ marginLeft: 4 }}>{statusInfo.label}</span>
                </Tag>
                <Text strong>
                  步骤 {entry.sequence}: {descriptor}
                </Text>
              </Space>
              {entry.message && (
                <div style={{ marginTop: 4 }}>
                  <Text>{entry.message}</Text>
                </div>
              )}
              {durationMs !== null && durationMs >= 0 && (
                <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                  耗时：{durationMs < 1000 ? `${durationMs}ms` : `${(durationMs / 1000).toFixed(1)}s`}
                </div>
              )}
              {timestamp && (
                <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                  {dayjs(timestamp).format('HH:mm:ss')}
                </div>
              )}
              {entry.details && <LogMetadata metadata={entry.details as Record<string, any>} />}
            </div>
          );
        })}
      </Space>
    </div>
  );
};

export const LogList: React.FC<{ logs: JobLogEvent[]; missingJob: boolean }> = ({ logs, missingJob }) => {
  if (missingJob) {
    return (
      <Alert
        type="warning"
        message="无法加载日志"
        description="对应的后台任务已清理或不存在。"
        showIcon
      />
    );
  }
  if (!logs.length) {
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        暂无日志输出。
      </Text>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {logs.map((log, index) => {
        const color = levelColorMap[log.level?.toLowerCase()] ?? 'default';
        return (
          <div key={`${log.timestamp ?? 'log'}_${index}`} style={{ lineHeight: 1.4 }}>
            <Space size="small" align="start">
              <Tag color={color} style={{ marginRight: 0 }}>
                {log.level?.toUpperCase() ?? 'INFO'}
              </Tag>
              <div>
                <Text style={{ fontWeight: 500 }}>{log.message}</Text>
                <div style={{ fontSize: 12, color: '#999' }}>
                  {log.timestamp ? dayjs(log.timestamp).format('HH:mm:ss') : ''}
                </div>
                <LogMetadata metadata={log.metadata as Record<string, any>} />
              </div>
            </Space>
          </div>
        );
      })}
    </div>
  );
};
