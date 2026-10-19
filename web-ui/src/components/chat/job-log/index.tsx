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
import 'dayjs/locale/zh-cn';

import { statusMeta, jobTypeMeta, FINAL_STATUSES } from './constants';
import type { JobLogPanelProps } from './constants';
import { useJobLogStream } from './useJobLogStream';
import { ActionLogs, LogList } from './LogSection';
import { ResultSummary, ProgressBar } from './ResultSection';
import { ThinkingProcess } from '@components/chat/ThinkingProcess';
import { RepairProgressBadge } from './RepairProgressBadge';
import { parseServerTimeDayjs } from '@/utils/serverTime';
import { useT } from '@/i18n/useT';
import { dayjsLocale } from '@/i18n/format';

dayjs.extend(relativeTime);

const { Text, Paragraph } = Typography;

const JobLogPanel: React.FC<JobLogPanelProps> = ({ jobId, initialJob, targetTaskName, planId, jobType: initialJobType, defaultExpanded = false, compact = false }) => {
  const t = useT();
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

  React.useEffect(() => {
    if (defaultExpanded) setExpanded(true);
  }, [jobId, defaultExpanded, setExpanded]);

  const statusInfo = statusMeta[status] || statusMeta.queued;

  const jobTypeInfo = React.useMemo(() => jobTypeMeta[jobType] ?? jobTypeMeta.default, [jobType]);

  const headerTitle = React.useMemo(() => {
    return (
      <Space size="small">
        <Tag color={statusInfo.color} style={{ marginRight: 0 }}>
          <Space size={4}>
            {statusInfo.icon}
            <span>{t(statusInfo.labelKey)}</span>
          </Space>
        </Tag>
        <Tag color={jobTypeInfo.color} style={{ marginRight: 0 }}>
          {t(jobTypeInfo.labelKey)}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          #{jobId.slice(0, 8)}
        </Text>
        <RepairProgressBadge logs={logs} jobStatus={status} />
      </Space>
    );
  }, [jobId, statusInfo, jobTypeInfo, logs, status, t]);

  const lastUpdatedText = React.useMemo(() => {
    const parsed = parseServerTimeDayjs(lastUpdatedAt);
    return parsed?.isValid() ? parsed.locale(dayjsLocale()).fromNow() : null;
  }, [lastUpdatedAt, t]);

  const lastControlText = React.useMemo(() => {
    if (!lastRuntimeControlAction || !lastRuntimeControlAt) return null;
    const actionLabel =
      lastRuntimeControlAction === 'pause'
        ? t('jobLog.actionPaused')
        : lastRuntimeControlAction === 'resume'
        ? t('jobLog.actionResumed')
        : t('jobLog.skipped');
    return `${actionLabel} ${dayjs(lastRuntimeControlAt).locale(dayjsLocale()).fromNow()}`;
  }, [lastRuntimeControlAction, lastRuntimeControlAt, t]);

  return (
    <>
      <Card
        size="small"
        style={{ marginTop: 12 }}
        title={headerTitle}
        extra={
          <Space size="small">
            <Tooltip title={isStreaming ? t('jobLog.syncing') : t('jobLog.polling')}>
              {isStreaming ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            </Tooltip>
            <Tooltip title={t('jobLog.rawLogTooltip')}>
              <Button
                type="link"
                size="small"
                icon={<FileTextOutlined />}
                onClick={() => setCliLogVisible(true)}
              >
                {t('jobLog.rawLog')}
              </Button>
            </Tooltip>
            <Button
              type="link"
              size="small"
              icon={expanded ? <UpOutlined /> : <DownOutlined />}
              onClick={() => setExpanded((prev) => !prev)}
            >
              {expanded ? t('common.collapse') : t('common.expand')}
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
                  {t('jobLog.currentTask')}
                </Text>
                <Text>{targetTaskName ?? '-'}</Text>
              </Space>
              {resolvedPlanId !== null && resolvedPlanId !== undefined ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('jobLog.planId', { id: resolvedPlanId })}
                </Text>
              ) : planId !== undefined && planId !== null ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('jobLog.planId', { id: planId })}
                </Text>
              ) : null}
              {jobMetadata?.session_id && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('jobLog.chatId', { id: jobMetadata.session_id })}
                </Text>
              )}
              {!compact && lastUpdatedText && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t('jobLog.lastUpdated', { time: lastUpdatedText })}
                </Text>
              )}
            </Space>

            {error && (
              <Alert
                type="error"
                message={t('jobLog.backgroundFailed')}
                description={error}
                showIcon
              />
            )}

            {!error && !FINAL_STATUSES.has(status) && thinkingProcess.steps.length > 0 && (
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
                    ? t('jobLog.paused')
                    : lastRuntimeControlAction === 'skip_step'
                    ? t('jobLog.skipped')
                    : t('jobLog.runningTitle')
                }
                description={
                  streamPaused
                    ? t('jobLog.pausedDesc')
                    : lastRuntimeControlAction === 'skip_step'
                    ? t('jobLog.skippedDesc')
                    : t('jobLog.runningDesc')
                }
                showIcon
              />
            )}
            {!error && lastControlText && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('jobLog.lastAction', { text: lastControlText })}
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
                    message.warning(resp.message || t('jobLog.cannotPause'));
                  } else {
                    message.success(t('jobLog.paused'));
                  }
                }}
                onResume={async () => {
                  const resp = await resumeExecution();
                  if (!resp.success) {
                    message.warning(resp.message || t('jobLog.cannotResume'));
                  } else {
                    message.success(t('jobLog.runResumed'));
                  }
                }}
                onSkipStep={async () => {
                  const resp = await skipCurrentStep();
                  if (!resp.success) {
                    message.warning(resp.message || t('jobLog.cannotSkip'));
                  } else {
                    message.success(t('jobLog.skipped'));
                  }
                }}
              />
            )}
            {!compact && <ActionLogs actionLogs={actionLogs} />}
            {!compact && <LogList logs={logs} missingJob={missingJob} />}
            <ResultSummary result={result} jobType={jobType} />

            {!compact && Object.keys(stats || {}).length > 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                <Divider plain style={{ margin: '12px 0' }}>
                  {t('jobLog.runStats')}
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
        title={t('jobLog.cliTitle')}
        footer={
          <Space size="small">
            <Button onClick={() => setCliLogVisible(false)}>{t('common.close')}</Button>
            <Button type="primary" onClick={fetchCliLog} disabled={cliLogLoading}>
              {t('common.refresh')}
            </Button>
          </Space>
        }
      >
        {cliLogPath && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('jobLog.logPath', { path: cliLogPath })}
          </Text>
        )}
        {cliLogError && (
          <Alert
            type="warning"
            message={t('jobLog.cliUnavailable')}
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
              background: 'var(--surface-log)',
              color: 'var(--on-log)',
              padding: 12,
              borderRadius: 6,
              fontSize: 12,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {cliLogLines.length ? cliLogLines.join('\n') : t('jobLog.noCliOutput')}
          </pre>
        )}
        {cliLogTruncated && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('jobLog.truncated')}
          </Text>
        )}
      </Modal>
    </>
  );
};

export default JobLogPanel;
