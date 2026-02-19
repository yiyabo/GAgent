import * as React from 'react';
import { Card, Button, Space, Tag, Typography, Tooltip, Alert, Divider, Modal, Spin } from 'antd';
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

dayjs.extend(relativeTime);

const { Text, Paragraph } = Typography;

const JobLogPanel: React.FC<JobLogPanelProps> = ({ jobId, initialJob, targetTaskName, planId, jobType: initialJobType }) => {
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

  return (
    <>
      <Card
        size="small"
        style={{ marginTop: 12 }}
        title={headerTitle}
        extra={
          <Space size="small">
            <Tooltip title={isStreaming ? '实时同步中' : '使用轮询获取'}>
              {isStreaming ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            </Tooltip>
            <Tooltip title="查看 Claude Code 日志">
              <Button
                type="link"
                size="small"
                icon={<FileTextOutlined />}
                onClick={() => setCliLogVisible(true)}
              >
                CLI 日志
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
                  目标任务：
                </Text>
                <Text>{targetTaskName ?? '-'}</Text>
              </Space>
              {resolvedPlanId !== null && resolvedPlanId !== undefined ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  计划 ID：{resolvedPlanId}
                </Text>
              ) : planId !== undefined && planId !== null ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  计划 ID：{planId}
                </Text>
              ) : null}
              {jobMetadata?.session_id && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  会话 ID：{jobMetadata.session_id}
                </Text>
              )}
              {lastUpdatedText && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  最近更新：{lastUpdatedText}
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

            <ProgressBar jobType={jobType} status={status} logs={logs} stats={stats} jobParams={jobParams} />
            <ActionLogs actionLogs={actionLogs} />
            <LogList logs={logs} missingJob={missingJob} />
            <ResultSummary result={result} jobType={jobType} />

            {Object.keys(stats || {}).length > 0 && (
              <div style={{ fontSize: 12, color: '#999' }}>
                <Divider plain style={{ margin: '12px 0' }}>
                  统计信息
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
        title="Claude Code CLI 日志"
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
            日志路径：{cliLogPath}
          </Text>
        )}
        {cliLogError && (
          <Alert
            type="warning"
            message="无法加载 CLI 日志"
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
            {cliLogLines.length ? cliLogLines.join('\n') : '暂无 CLI 日志输出。'}
          </pre>
        )}
        {cliLogTruncated && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            已仅展示最新 200 行。
          </Text>
        )}
      </Modal>
    </>
  );
};

export default JobLogPanel;
