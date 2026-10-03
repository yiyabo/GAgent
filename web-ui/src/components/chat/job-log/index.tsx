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

dayjs.extend(relativeTime);

const { Text, Paragraph } = Typography;

const JobLogPanel: React.FC<JobLogPanelProps> = ({ jobId, initialJob, targetTaskName, planId, jobType: initialJobType, defaultExpanded = false }) => {
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
    return dayjs(lastUpdatedAt).locale('zh-cn').fromNow();
  }, [lastUpdatedAt]);

  const lastControlText = React.useMemo(() => {
    if (!lastRuntimeControlAction || !lastRuntimeControlAt) return null;
    const actionLabel =
      lastRuntimeControlAction === 'pause'
        ? '已暂停'
        : lastRuntimeControlAction === 'resume'
        ? '已恢复'
        : '已跳过当前步骤';
    return `${actionLabel} ${dayjs(lastRuntimeControlAt).locale('zh-cn').fromNow()}`;
  }, [lastRuntimeControlAction, lastRuntimeControlAt]);

  return (
    <>
      <Card
        size="small"
        style={{ marginTop: 12 }}
        title={headerTitle}
        extra={
          <Space size="small">
            <Tooltip title={isStreaming ? '正在实时同步' : '定时刷新中'}>
              {isStreaming ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            </Tooltip>
            <Tooltip title="查看执行器原始日志">
              <Button
                type="link"
                size="small"
                icon={<FileTextOutlined />}
                onClick={() => setCliLogVisible(true)}
              >
                原始日志
              </Button>
            </Tooltip>
            <Button
              type="link"
              size="small"
              icon={expanded ? <UpOutlined /> : <DownOutlined />}
              onClick={() => setExpanded((prev) => !prev)}
            >
              {expanded ? '收起' : '展开'}
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
                  当前任务：
                </Text>
                <Text>{targetTaskName ?? '-'}</Text>
              </Space>
              {resolvedPlanId !== null && resolvedPlanId !== undefined ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  计划编号： {resolvedPlanId}
                </Text>
              ) : planId !== undefined && planId !== null ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  计划编号： {planId}
                </Text>
              ) : null}
              {jobMetadata?.session_id && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  会话编号： {jobMetadata.session_id}
                </Text>
              )}
              {lastUpdatedText && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  最近更新： {lastUpdatedText}
                </Text>
              )}
            </Space>

            {error && (
              <Alert
                type="error"
                message="后台执行失败"
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
                    ? '执行已暂停'
                    : lastRuntimeControlAction === 'skip_step'
                    ? '已跳过当前步骤'
                    : '正在执行'
                }
                description={
                  streamPaused
                    ? '深度思考已暂停，点击恢复继续执行。'
                    : lastRuntimeControlAction === 'skip_step'
                    ? 'Agent 已跳过当前推理分支，继续执行下一步。'
                    : '正在处理任务，执行步骤会实时更新。'
                }
                showIcon
              />
            )}
            {!error && lastControlText && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                最近操作： {lastControlText}
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
                    message.warning(resp.message || '暂时无法暂停');
                  } else {
                    message.success('执行已暂停');
                  }
                }}
                onResume={async () => {
                  const resp = await resumeExecution();
                  if (!resp.success) {
                    message.warning(resp.message || '暂时无法恢复');
                  } else {
                    message.success('执行已恢复');
                  }
                }}
                onSkipStep={async () => {
                  const resp = await skipCurrentStep();
                  if (!resp.success) {
                    message.warning(resp.message || '暂时无法跳过此步骤');
                  } else {
                    message.success('已跳过当前步骤');
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
                  运行统计
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
        title="执行器原始日志"
        footer={
          <Space size="small">
            <Button onClick={() => setCliLogVisible(false)}>关闭</Button>
            <Button type="primary" onClick={fetchCliLog} disabled={cliLogLoading}>
              刷新
            </Button>
          </Space>
        }
      >
        {cliLogPath && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            日志路径： {cliLogPath}
          </Text>
        )}
        {cliLogError && (
          <Alert
            type="warning"
            message="暂时无法读取执行器日志"
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
            {cliLogLines.length ? cliLogLines.join('\n') : '尚无执行器日志输出。'}
          </pre>
        )}
        {cliLogTruncated && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            仅展示最新的 200 行日志。
          </Text>
        )}
      </Modal>
    </>
  );
};

export default JobLogPanel;
