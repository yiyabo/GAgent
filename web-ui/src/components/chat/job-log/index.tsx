import * as React from 'react';
import { App as AntdApp, Card, Button, Space, Tag, Typography, Tooltip, Alert, Divider, Modal, Spin } from 'antd';
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  DownOutlined,
  UpOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

import { statusMeta, jobTypeMeta, FINAL_STATUSES } from './constants';
import type { JobLogPanelProps } from './constants';
import { useJobLogStream } from './useJobLogStream';
import { ActionLogs, LogList } from './LogSection';
import { ResultSummary, ProgressBar } from './ResultSection';
import { ThinkingProcess } from '@components/chat/ThinkingProcess';

dayjs.extend(relativeTime);

const { Text, Paragraph } = Typography;

const JobLogPanel: React.FC<JobLogPanelProps> = ({ jobId, initialJob, targetTaskName, planId, jobType: initialJobType }) => {
  const { message } = AntdApp.useApp();
  const {
    logs,
    actionLogs,
    status,
    stats,
    jobParams,
    result,
    error,
    expanded,
    setExpanded,
    isStreaming,
    lastUpdatedAt,
    missingJob,
    jobType,
    jobMetadata,
    resolvedPlanId,
    cliLogVisible,
    setCliLogVisible,
    cliLogLines,
    cliLogLoading,
    cliLogError,
    cliLogTruncated,
    cliLogPath,
    fetchCliLog,
    thinkingProcess,
    streamPaused,
    lastRuntimeControlAction,
    lastRuntimeControlAt,
    runtimeControlBusy,
    runtimeControlBusyAction,
    pauseExecution,
    resumeExecution,
    skipCurrentStep,
  } = useJobLogStream({ jobId, initialJob, planId, jobType: initialJobType });

  const statusInfo = statusMeta[status] || statusMeta.queued;

  const jobTypeInfo = React.useMemo(() => jobTypeMeta[jobType] ?? jobTypeMeta.default, [jobType]);

  const headerTitle = React.useMemo(() => {
    return (
      <Space size="small">
        <Tag color={statusInfo.color} style={{ marginRight: 0 }}>
          <Space size={4}>
            {statusInfo.icon}
            <span>{statusInfo.label}</span>
          </Space>
        </Tag>
        <Tag color={jobTypeInfo.color} style={{ marginRight: 0 }}>
          {jobTypeInfo.label}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          #{jobId.slice(0, 8)}
        </Text>
      </Space>
    );
  }, [jobId, statusInfo, jobTypeInfo]);

  const lastUpdatedText = React.useMemo(() => {
    if (!lastUpdatedAt) return null;
    return dayjs(lastUpdatedAt).fromNow();
  }, [lastUpdatedAt]);

  const lastControlText = React.useMemo(() => {
    if (!lastRuntimeControlAction || !lastRuntimeControlAt) return null;
    const actionLabel =
      lastRuntimeControlAction === 'pause'
        ? 'Paused'
        : lastRuntimeControlAction === 'resume'
        ? 'Resumed'
        : 'Skipped step';
    return `${actionLabel} ${dayjs(lastRuntimeControlAt).fromNow()}`;
  }, [lastRuntimeControlAction, lastRuntimeControlAt]);

  return (
    <>
      <Card
        size="small"
        style={{ marginTop: 12 }}
        title={headerTitle}
        extra={
          <Space size="small">
            <Tooltip title={isStreaming ? 'Real-time sync active' : 'Using polling'}>
              {isStreaming ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            </Tooltip>
            <Tooltip title="View Claude Code logs">
              <Button
                type="link"
                size="small"
                icon={<FileTextOutlined />}
                onClick={() => setCliLogVisible(true)}
              >
                CLI Logs
              </Button>
            </Tooltip>
            <Button
              type="link"
              size="small"
              icon={expanded ? <UpOutlined /> : <DownOutlined />}
              onClick={() => setExpanded((prev) => !prev)}
            >
              {expanded ? 'Collapse' : 'Expand'}
            </Button>
          </Space>
        }
        styles={{
          body: expanded
            ? { paddingTop: 12, paddingBottom: 12 }
            : { paddingTop: 0, paddingBottom: 0 },
        }}
      >
        {expanded && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Space size="small">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Target task:
                </Text>
                <Text>{targetTaskName ?? '-'}</Text>
              </Space>
              {resolvedPlanId !== null && resolvedPlanId !== undefined ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Plan ID: {resolvedPlanId}
                </Text>
              ) : planId !== undefined && planId !== null ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Plan ID: {planId}
                </Text>
              ) : null}
              {jobMetadata?.session_id && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Session ID: {jobMetadata.session_id}
                </Text>
              )}
              {lastUpdatedText && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Last updated: {lastUpdatedText}
                </Text>
              )}
            </Space>

            {error && (
              <Alert
                type="error"
                message="Background execution failed"
                description={error}
                showIcon
              />
            )}

            {!error && thinkingProcess.steps.length > 0 && (
              <Alert
                type={
                  streamPaused
                    ? 'warning'
                    : lastRuntimeControlAction === 'skip_step'
                    ? 'success'
                    : 'info'
                }
                message={
                  streamPaused
                    ? 'Execution paused'
                    : lastRuntimeControlAction === 'skip_step'
                    ? 'Current step skipped'
                    : 'Execution running'
                }
                description={
                  streamPaused
                    ? 'Deep Think is paused. Click Resume to continue.'
                    : lastRuntimeControlAction === 'skip_step'
                    ? 'The agent skipped the current reasoning branch and moved to the next step.'
                    : 'Deep Think is processing and streaming structured steps in real time.'
                }
                showIcon
              />
            )}
            {!error && lastControlText && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                Last control action: {lastControlText}
              </Text>
            )}

            <ProgressBar jobType={jobType} status={status} logs={logs} stats={stats} jobParams={jobParams} />
            {thinkingProcess.steps.length > 0 && (
              <ThinkingProcess
                process={thinkingProcess}
                isFinished={FINAL_STATUSES.has(status)}
                canControl
                paused={streamPaused}
                controlDisabled={FINAL_STATUSES.has(status)}
                controlBusy={runtimeControlBusy}
                controlBusyAction={runtimeControlBusyAction}
                onPause={async () => {
                  const resp = await pauseExecution();
                  if (!resp.success) {
                    message.warning(resp.message || 'Pause not available');
                  } else {
                    message.success('Execution paused');
                  }
                }}
                onResume={async () => {
                  const resp = await resumeExecution();
                  if (!resp.success) {
                    message.warning(resp.message || 'Resume not available');
                  } else {
                    message.success('Execution resumed');
                  }
                }}
                onSkipStep={async () => {
                  const resp = await skipCurrentStep();
                  if (!resp.success) {
                    message.warning(resp.message || 'Skip step not available');
                  } else {
                    message.success('Current step skipped');
                  }
                }}
              />
            )}
            <ActionLogs actionLogs={actionLogs} />
            <LogList logs={logs} missingJob={missingJob} />
            <ResultSummary result={result} jobType={jobType} />

            {Object.keys(stats || {}).length > 0 && (
              <div style={{ fontSize: 12, color: '#999' }}>
                <Divider plain style={{ margin: '12px 0' }}>
                  Statistics
                </Divider>
                <Paragraph
                  copyable={{
                    text: JSON.stringify(stats, null, 2),
                  }}
                  style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
                >
                  {JSON.stringify(stats, null, 2)}
                </Paragraph>
              </div>
            )}
          </Space>
        )}
      </Card>

      <Modal
        open={cliLogVisible}
        onCancel={() => setCliLogVisible(false)}
        title="Claude Code CLI Logs"
        footer={
          <Space size="small">
            <Button onClick={() => setCliLogVisible(false)}>Close</Button>
            <Button type="primary" onClick={fetchCliLog} disabled={cliLogLoading}>
              Refresh
            </Button>
          </Space>
        }
      >
        {cliLogPath && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            Log path: {cliLogPath}
          </Text>
        )}
        {cliLogError && (
          <Alert
            type="warning"
            message="Unable to load CLI logs"
            description={cliLogError}
            showIcon
            style={{ marginTop: 12 }}
          />
        )}
        {cliLogLoading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '24px 0' }}>
            <Spin />
          </div>
        ) : (
          <pre
            style={{
              marginTop: 12,
              maxHeight: 360,
              overflow: 'auto',
              background: '#111827',
              color: '#E5E7EB',
              padding: 12,
              borderRadius: 6,
              fontSize: 12,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {cliLogLines.length ? cliLogLines.join('\n') : 'No CLI log output yet.'}
          </pre>
        )}
        {cliLogTruncated && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            Showing only the latest 200 lines.
          </Text>
        )}
      </Modal>
    </>
  );
};

export default JobLogPanel;
