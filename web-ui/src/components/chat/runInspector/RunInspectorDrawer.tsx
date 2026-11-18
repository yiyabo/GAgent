import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Collapse, Descriptions, Drawer, Empty, Segmented, Select, Space, Spin, Tag, Typography } from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { chatApi } from '@api/chat';
import { useT } from '@/i18n/useT';
import type { RunInspectionChildRun, RunInspectionStep, RunInspectionTarget } from '@/types/runInspection';
import {
  exportBlob,
  exportFileName,
  filterSteps,
  formatDuration,
  formatTokens,
  stepTone,
  type StepFilter,
} from './model';

const { Text, Paragraph } = Typography;

interface RunInspectorDrawerProps {
  open: boolean;
  sessionId: string | null;
  target: RunInspectionTarget | null;
  onClose: () => void;
}

const preStyle: React.CSSProperties = {
  margin: 0,
  padding: 8,
  maxHeight: 240,
  overflow: 'auto',
  fontSize: 12,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  background: 'var(--bg-tertiary)',
  borderRadius: 6,
};

const toneColor: Record<ReturnType<typeof stepTone>, string> = {
  success: 'green',
  error: 'red',
  processing: 'blue',
  default: 'default',
};

const StepTitle: React.FC<{ step: RunInspectionStep }> = ({ step }) => {
  const t = useT();
  const names = step.tools.map((call) => call.tool);
  return (
    <Space size={8} wrap>
      <Text type="secondary">#{step.iteration ?? '—'}</Text>
      <Tag color={toneColor[stepTone(step)]}>
        {step.kind === 'tool' ? names.join(', ') || t('runInspector.tool') : t('runInspector.reasoning')}
      </Tag>
      {step.success === false && <Tag color="red">{t('runInspector.failed')}</Tag>}
      <Text type="secondary">{formatDuration(step.duration_ms)}</Text>
      {step.label && <Text ellipsis style={{ maxWidth: 360 }}>{step.label}</Text>}
    </Space>
  );
};

/** Short, readable handle for a child run id like ``chat_run_42:sub_ab12cd34ef56``. */
const childRunLabel = (runId: string): string => {
  const marker = runId.lastIndexOf(':sub_');
  const suffix = marker >= 0 ? runId.slice(marker + 5) : runId;
  return suffix.length > 8 ? suffix.slice(-8) : suffix;
};

const childRole = (child: RunInspectionChildRun): string => {
  const purpose = child.call_purposes?.find((value) => value.startsWith('subagent_')) ?? child.call_purposes?.[0];
  return purpose ? purpose.replace(/^subagent_/, '') : '—';
};

const ChildRunsSection: React.FC<{ childRuns: RunInspectionChildRun[] }> = ({ childRuns }) => {
  const t = useT();
  return (
    <div data-testid="run-inspector-child-runs">
      <Text strong>{t('runInspector.subagents', { count: childRuns.length })}</Text>
      <ul style={{ paddingLeft: 18, marginTop: 8 }}>
        {childRuns.map((child) => (
          <li key={child.child_run_id}>
            <Text code title={child.child_run_id}>{childRunLabel(child.child_run_id)}</Text>
            <Tag style={{ marginLeft: 8 }}>{childRole(child)}</Tag>
            <Text type="secondary">
              {t('runInspector.usageRow', {
                calls: child.call_count,
                tokens: formatTokens(child.total_tokens),
                duration: formatDuration(child.duration_ms),
              })}
            </Text>
            {(child.error_calls ?? 0) > 0 && (
              <Tag color="red" style={{ marginLeft: 8 }}>{t('runInspector.failedCalls', { count: child.error_calls })}</Tag>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
};

const RunInspectorDrawer: React.FC<RunInspectorDrawerProps> = ({ open, sessionId, target, onClose }) => {
  const t = useT();
  const [filter, setFilter] = useState<StepFilter>('all');
  const [tool, setTool] = useState<string | null>(null);
  const enabled = open && Boolean(sessionId) && Boolean(target);
  const query = useQuery({
    queryKey: ['run-inspection', sessionId, target?.run_id ?? null, target?.message_id ?? null],
    queryFn: () => chatApi.getRunInspection(sessionId as string, target as RunInspectionTarget),
    enabled,
    staleTime: 5_000,
    retry: false,
  });
  const inspection = query.data;
  const steps = useMemo(() => (inspection ? filterSteps(inspection.steps, filter, tool) : []), [inspection, filter, tool]);

  const download = () => {
    if (!inspection) return;
    const url = URL.createObjectURL(exportBlob(inspection));
    const link = document.createElement('a');
    link.href = url;
    link.download = exportFileName(inspection);
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Drawer
      title={t('runInspector.title')}
      placement="right"
      width="min(760px, 96vw)"
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void query.refetch()} disabled={!enabled}>
            {t('runInspector.refresh')}
          </Button>
          <Button size="small" type="primary" icon={<DownloadOutlined />} onClick={download} disabled={!inspection}>
            {t('runInspector.export')}
          </Button>
        </Space>
      }
    >
      {!target && <Empty description={t('runInspector.noRun')} />}
      {target && query.isLoading && <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>}
      {target && query.isError && (
        <Alert type="error" showIcon message={t('runInspector.loadFailed')}
          description={query.error instanceof Error ? query.error.message : String(query.error)} />
      )}
      {inspection && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label={t('runInspector.status')}>
              <Tag color={inspection.run.status === 'succeeded' ? 'green' : inspection.run.status === 'failed' ? 'red' : 'blue'}>
                {inspection.run.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('runInspector.duration')}>{formatDuration(inspection.run.duration_ms)}</Descriptions.Item>
            <Descriptions.Item label={t('runInspector.steps')}>
              {t('runInspector.stepCounts', { steps: inspection.summary.steps, tools: inspection.summary.tool_steps })}
            </Descriptions.Item>
            <Descriptions.Item label={t('runInspector.toolFailures')}>
              <Text type={inspection.summary.tool_failures ? 'danger' : undefined}>{inspection.summary.tool_failures}</Text>
            </Descriptions.Item>
            <Descriptions.Item label={t('runInspector.tokens')}>
              {t('runInspector.tokenSummary', {
                total: formatTokens(inspection.usage.total_tokens),
                cached: formatTokens(inspection.usage.cache_read_tokens),
                calls: inspection.usage.calls,
              })}
            </Descriptions.Item>
            <Descriptions.Item label={t('runInspector.artifacts')}>{inspection.summary.artifacts}</Descriptions.Item>
            <Descriptions.Item label={t('runInspector.runId')} span={2}>
              <Text copyable code>{inspection.run.run_id}</Text>
            </Descriptions.Item>
            {inspection.run.message && (
              <Descriptions.Item label={t('runInspector.request')} span={2}>
                <Paragraph ellipsis={{ rows: 3, expandable: true }} style={{ marginBottom: 0 }}>{inspection.run.message}</Paragraph>
              </Descriptions.Item>
            )}
            {typeof inspection.final.termination_reason === 'string' && (
              <Descriptions.Item label={t('runInspector.termination')} span={2}>{inspection.final.termination_reason}</Descriptions.Item>
            )}
          </Descriptions>

          {inspection.run.error && <Alert type="error" showIcon message={inspection.run.error} />}
          {(inspection.truncated.steps || inspection.truncated.artifacts) && (
            <Alert type="warning" showIcon message={t('runInspector.truncated')} />
          )}

          <Space wrap>
            <Segmented
              value={filter}
              onChange={(value) => setFilter(value as StepFilter)}
              options={[
                { value: 'all', label: t('runInspector.filterAll') },
                { value: 'tools', label: t('runInspector.filterTools') },
                { value: 'failures', label: t('runInspector.filterFailures') },
              ]}
            />
            <Select
              allowClear
              size="middle"
              style={{ minWidth: 200 }}
              placeholder={t('runInspector.filterTool')}
              value={tool ?? undefined}
              onChange={(value) => setTool(value ?? null)}
              options={inspection.summary.tools.map((name) => ({ value: name, label: name }))}
            />
            <Text type="secondary">{t('runInspector.showing', { shown: steps.length, total: inspection.steps.length })}</Text>
          </Space>

          {steps.length === 0 ? (
            <Empty description={t('runInspector.noSteps')} />
          ) : (
            <Collapse
              size="small"
              items={steps.map((step) => ({
                key: `${step.first_seq}`,
                label: <StepTitle step={step} />,
                children: (
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {step.tools.map((call, index) => (
                      <div key={`${call.tool_call_id ?? call.tool}-${index}`}>
                        <Text strong>{call.tool}</Text>
                        {call.params && <pre style={preStyle}>{call.params}</pre>}
                      </div>
                    ))}
                    {step.result && (
                      <div>
                        <Text strong>{t('runInspector.result')}</Text>
                        <pre style={preStyle}>{step.result}</pre>
                      </div>
                    )}
                    {step.self_correction && <Alert type="info" showIcon message={step.self_correction} />}
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t('runInspector.stepMeta', { status: step.status ?? '—', start: step.started_at ?? '—', end: step.finished_at ?? '—' })}
                    </Text>
                  </Space>
                ),
              }))}
            />
          )}

          {inspection.events.length > 0 && (
            <div>
              <Text strong>{t('runInspector.events')}</Text>
              <ul style={{ paddingLeft: 18, marginTop: 8 }}>
                {inspection.events.map((event) => (
                  <li key={event.seq}>
                    <Tag color={event.type === 'error' ? 'red' : event.type === 'artifact' ? 'purple' : 'default'}>{event.type}</Tag>
                    <Text>{event.display_name ?? event.path ?? event.message ?? event.status ?? ''}</Text>
                    {event.source_tool && <Text type="secondary"> · {event.source_tool}</Text>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {inspection.usage.by_purpose.length > 0 && (
            <div>
              <Text strong>{t('runInspector.usage')}</Text>
              <ul style={{ paddingLeft: 18, marginTop: 8 }}>
                {inspection.usage.by_purpose.map((group) => (
                  <li key={`${group.phase}:${group.purpose}`}>
                    <Text>{group.purpose}</Text>
                    <Text type="secondary">
                      {' '}· {t('runInspector.usageRow', {
                        calls: group.calls,
                        tokens: formatTokens(group.total_tokens),
                        duration: formatDuration(group.duration_ms),
                      })}
                    </Text>
                    {group.failed > 0 && <Tag color="red" style={{ marginLeft: 8 }}>{t('runInspector.failedCalls', { count: group.failed })}</Tag>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(inspection.child_runs?.length ?? 0) > 0 && <ChildRunsSection childRuns={inspection.child_runs ?? []} />}

          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('runInspector.streamingNote', {
              thinking: inspection.summary.streaming_events.thinking_delta ?? 0,
              reasoning: inspection.summary.streaming_events.reasoning_delta ?? 0,
              deltas: inspection.summary.streaming_events.delta ?? 0,
            })}
          </Text>
        </Space>
      )}
    </Drawer>
  );
};

export default RunInspectorDrawer;
