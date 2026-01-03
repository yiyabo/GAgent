import React, { useMemo, useState } from 'react';
import {
  Avatar,
  Typography,
  Space,
  Button,
  Tooltip,
  message as antMessage,
  Drawer,
  Tag,
  Divider,
} from 'antd';
import {
  UserOutlined,
  RobotOutlined,
  InfoCircleOutlined,
  CopyOutlined,
  ReloadOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { ChatMessage as ChatMessageType, ToolResultPayload } from '@/types';
import { useChatStore } from '@store/chat';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ToolResultCard from './ToolResultCard';
import JobLogPanel from './JobLogPanel';
import { ThinkingProcess } from './ThinkingProcess';
import { MarkdownRenderer } from './MarkdownRenderer';
import { TypingIndicator } from './TypingIndicator';
import type { DecompositionJobStatus } from '@/types';


const { Text } = Typography;

interface ChatMessageProps {
  message: ChatMessageType;
}

const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const { type, content, timestamp, metadata } = message;
  const { saveMessageAsMemory, retryActionRun, retryLastMessage, isProcessing } = useChatStore();
  const [isSaving, setIsSaving] = useState(false);
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false);
  const [pendingDetailOpen, setPendingDetailOpen] = useState(false);
  const [processOpen, setProcessOpen] = useState(false);
  const [toolOpen, setToolOpen] = useState(false);
  const unifiedStream = Boolean(metadata && (metadata as any).unified_stream);
  const planMessage =
    unifiedStream && typeof (metadata as any)?.plan_message === 'string'
      ? ((metadata as any).plan_message as string)
      : null;
  const shouldAutoOpenProcess =
    unifiedStream && (metadata?.status === 'pending' || metadata?.status === 'running');

  React.useEffect(() => {
    if (shouldAutoOpenProcess) {
      setProcessOpen(true);
    }
  }, [shouldAutoOpenProcess]);

  const toolResults: ToolResultPayload[] = useMemo(
    () =>
      Array.isArray(metadata?.tool_results)
        ? (metadata?.tool_results as ToolResultPayload[])
        : [],
    [metadata?.tool_results],
  );
  const analysisText =
    typeof (metadata as any)?.analysis_text === 'string'
      ? ((metadata as any)?.analysis_text as string)
      : '';
  const finalSummary =
    typeof (metadata as any)?.final_summary === 'string'
      ? ((metadata as any)?.final_summary as string)
      : (content && content.trim().length > 0 ? content : null);
  const displayText =
    analysisText && analysisText.trim().length > 0 ? analysisText : finalSummary;
  const processSummary =
    finalSummary &&
      displayText &&
      finalSummary.trim().length > 0 &&
      finalSummary.trim() !== displayText.trim()
      ? finalSummary
      : null;
  const status = metadata?.status;
  const isCompleted = status === 'completed' || status === 'failed';
  const isStreaming =
    unifiedStream && (status === 'pending' || status === 'running');

  // 如果是统一流且处于初始 pending 阶段、没有前置文案和动作，显示思考中动画
  if (
    unifiedStream &&
    metadata?.status === 'pending' &&
    !planMessage &&
    !(metadata as any)?.raw_actions?.length &&
    !(metadata as any)?.actions?.length &&
    (content?.trim?.() ?? '') === '' &&
    !analysisText
  ) {
    return <TypingIndicator message="思考中" showAvatar={true} />;
  }
  const hasFooterDivider =
    !unifiedStream &&
    (toolResults.length > 0 ||
      (!!metadata &&
        (metadata.type === 'job_log' ||
          metadata.plan_id !== undefined ||
          metadata.plan_title)));
  const isPendingAction =
    type === 'assistant' &&
    metadata?.status === 'pending' &&
    Array.isArray(metadata?.raw_actions) &&
    metadata.raw_actions.length > 0;

  const ghostTextStyle: React.CSSProperties = {
    color: 'var(--text-secondary)',
    opacity: 0.72,
    fontSize: 12,
    lineHeight: 1.5,
  };

  // 复制消息内容
  const handleCopy = () => {
    navigator.clipboard.writeText(content);
  };

  // 保存为记忆
  const handleSaveAsMemory = async () => {
    try {
      setIsSaving(true);
      await saveMessageAsMemory(message);
      antMessage.success('✅ 已保存为记忆');
    } catch (error) {
      console.error('保存记忆失败:', error);
      antMessage.error('❌ 保存失败');
    } finally {
      setIsSaving(false);
    }
  };

  // 格式化时间
  const formatTime = (date: Date) => {
    return new Date(date).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // 渲染头像 - Claude Code 风格
  const renderAvatar = () => {
    const avatarProps = {
      size: 28 as const,
      style: { flexShrink: 0 },
    };

    switch (type) {
      case 'user':
        return (
          <Avatar
            {...avatarProps}
            icon={<UserOutlined />}
            style={{
              ...avatarProps.style,
              background: 'var(--bg-tertiary)',
              borderRadius: 4,
            }}
          />
        );
      case 'assistant':
        return (
          <Avatar
            {...avatarProps}
            icon={<RobotOutlined />}
            style={{
              ...avatarProps.style,
              background: 'var(--primary-gradient)',
              borderRadius: 6,
            }}
          />
        );
      case 'system':
        return (
          <Avatar
            {...avatarProps}
            icon={<InfoCircleOutlined />}
            style={{
              ...avatarProps.style,
              background: 'var(--bg-tertiary)',
            }}
          />
        );
      default:
        return null;
    }
  };

  // 自定义代码块渲染
  const CodeBlock = ({ children, className }: { children: string; className?: string }) => {
    const language = className?.replace('lang-', '') || 'text';

    return (
      <SyntaxHighlighter
        style={tomorrow}
        language={language}
        PreTag="div"
        customStyle={{
          margin: '8px 0',
          borderRadius: '6px',
          fontSize: '13px',
        }}
      >
        {children}
      </SyntaxHighlighter>
    );
  };

  const renderPendingActions = () => {
    if (!isPendingAction) return null;
    if (unifiedStream) return null;
    const actions = Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : [];
    const visible = pendingDetailOpen ? actions : actions.slice(0, 3);

    return (
      <div
        style={{
          marginTop: 10,
          padding: '10px 12px',
          borderRadius: 'var(--radius-sm)',
          border: '1px dashed var(--border-color)',
          background: 'var(--bg-tertiary)',
          fontSize: 12,
        }}
      >
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Space align="center" size={8}>
            <div
              style={{
                width: 6,
                height: 6,
                borderRadius: 6,
                background: 'var(--primary-color)',
              }}
            />
            <Text type="secondary" style={{ opacity: 0.8, fontSize: 12 }}>
              正在规划 {actions.length} 个工具...
            </Text>
          </Space>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            {visible.map((act: any, idx: number) => {
              const kind = typeof act?.kind === 'string' ? act.kind : 'action';
              const name = typeof act?.name === 'string' ? act.name : '';
              const order = typeof act?.order === 'number' ? act.order : idx + 1;
              return (
                <div key={`${order}_${kind}_${name}`} style={{ marginBottom: 4 }}>
                  • 步骤 {order}: {kind}
                  {name ? ` / ${name}` : ''}
                </div>
              );
            })}
            {actions.length > 3 && !pendingDetailOpen && <Text type="secondary">…</Text>}
          </div>
          {actions.length > 3 && (
            <Button
              type="link"
              size="small"
              onClick={() => setPendingDetailOpen((v) => !v)}
              style={{ padding: 0 }}
            >
              {pendingDetailOpen ? '收起计划' : '展开全部计划'}
            </Button>
          )}
        </Space>
      </div>
    );
  };

  const formatToolActionLabel = (action: any) => {
    const kind = typeof action?.kind === 'string' ? action.kind : '';
    const name = typeof action?.name === 'string' ? action.name : '';
    const params = action?.parameters && typeof action.parameters === 'object' ? action.parameters : {};
    const query = typeof params.query === 'string' ? params.query.trim() : '';

    if (kind === 'tool_operation') {
      if (name && query) {
        return `调用 ${name} 搜索“${query}”`;
      }
      if (name) {
        return `调用 ${name}`;
      }
      return '调用工具';
    }
    if (kind === 'plan_operation') {
      const title =
        typeof params.plan_title === 'string'
          ? params.plan_title
          : typeof params.title === 'string'
            ? params.title
            : '';
      return title ? `执行计划：${title}` : `执行计划操作${name ? ` ${name}` : ''}`;
    }
    if (kind === 'task_operation') {
      const taskName =
        typeof params.task_name === 'string'
          ? params.task_name
          : typeof params.title === 'string'
            ? params.title
            : '';
      return taskName ? `执行任务：${taskName}` : `执行任务操作${name ? ` ${name}` : ''}`;
    }
    if (kind === 'context_request') {
      return `获取上下文${name ? `：${name}` : ''}`;
    }
    if (kind === 'system_operation') {
      return `系统操作${name ? `：${name}` : ''}`;
    }
    return `${kind || 'action'}${name ? `/${name}` : ''}`;
  };

  const deriveToolActionStatusIcon = (action: any) => {
    if (action?.status === 'completed' || action?.success === true) return '✅';
    if (action?.status === 'failed' || action?.success === false) return '⚠️';
    if (metadata?.status === 'completed') return '✅';
    if (metadata?.status === 'failed') return '⚠️';
    return '⏳';
  };

  const renderAnalysis = () => null;

  const renderToolProgress = () => {
    if (!unifiedStream) return null;
    const actions =
      (Array.isArray(metadata?.actions) ? metadata?.actions : null) ??
      (Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : []);
    if (!actions || actions.length === 0) return null;
    const visibleActions = actions;
    if (visibleActions.length === 0) return null;
    const toolActions = visibleActions.filter((act: any) => act?.kind === 'tool_operation');

    const statusLabel =
      status === 'completed'
        ? '已完成'
        : status === 'failed'
          ? '已失败'
          : '执行中';
    const statusColor =
      status === 'completed'
        ? 'green'
        : status === 'failed'
          ? 'red'
          : 'blue';

    const summary =
      toolActions.length > 0
        ? `工具：${toolActions
          .map((act: any) => (typeof act?.name === 'string' ? act.name : null))
          .filter(Boolean)
          .slice(0, 3)
          .join(', ')}${toolActions.length > 3 ? ` 等 ${toolActions.length} 个` : ''}`
        : `动作：${visibleActions.length} 个`;

    return (
      <div
        style={{
          marginBottom: 10,
          padding: '10px 12px',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-color)',
          background: 'var(--bg-tertiary)',
          fontSize: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Space size={8} align="center">
            <Tag color={statusColor} style={{ margin: 0 }}>
              {statusLabel}
            </Tag>
            <Text style={{ color: 'var(--text-secondary)' }}>{summary}</Text>
          </Space>
          <Button
            type="link"
            size="small"
            onClick={() => setToolOpen((v) => !v)}
            style={{ padding: 0, fontSize: 12 }}
          >
            {toolOpen ? '收起' : '查看过程'}
          </Button>
        </div>
        {toolOpen && (
          <div style={{ marginTop: 8 }}>
            {processSummary && (
              <div style={{ marginBottom: 8 }}>
                <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                  摘要：{processSummary}
                </Text>
              </div>
            )}
            {visibleActions.map((action: any, index: number) => {
              const label = formatToolActionLabel(action);
              const order = typeof action?.order === 'number' ? action.order : index + 1;
              const statusIcon = deriveToolActionStatusIcon(action);
              const createdTasks = Array.isArray(action?.details?.created)
                ? (action.details.created as Array<Record<string, any>>)
                : [];
              const singleTask = action?.details?.task && typeof action.details.task === 'object'
                ? (action.details.task as Record<string, any>)
                : null;
              return (
                <div
                  key={`${order}_${action?.name ?? 'action'}`}
                  style={{ color: 'var(--text-secondary)', marginBottom: 4, fontSize: 12 }}
                >
                  {statusIcon} 步骤 {order}: {label}
                  {singleTask && (
                    <div style={{ marginTop: 6, paddingLeft: 18 }}>
                      {typeof singleTask.name === 'string' && (
                        <div>子任务: {singleTask.name}</div>
                      )}
                      {typeof singleTask.instruction === 'string' && singleTask.instruction.trim().length > 0 && (
                        <div>说明: {singleTask.instruction}</div>
                      )}
                    </div>
                  )}
                  {createdTasks.length > 0 && (
                    <div style={{ marginTop: 6, paddingLeft: 18 }}>
                      {createdTasks.map((task, idx) => {
                        const name =
                          typeof task?.name === 'string'
                            ? task.name
                            : typeof task?.title === 'string'
                              ? task.title
                              : '';
                        const instruction =
                          typeof task?.instruction === 'string' ? task.instruction : '';
                        return (
                          <div key={`${order}_created_${idx}`} style={{ marginBottom: 6 }}>
                            {name ? <div>子任务: {name}</div> : null}
                            {instruction ? <div>说明: {instruction}</div> : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const renderSummary = () => {
    if (!displayText) return null;
    if (isStreaming) {
      return (
        <div
          style={{
            marginTop: 4,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {displayText}
          <span className="stream-cursor">▍</span>
        </div>
      );
    }
    return (
      <div style={{ marginTop: 4 }}>
        <MarkdownRenderer content={displayText} />
      </div>
    );
  };

  const renderUnifiedStatusLine = () => {
    return null;
  };

  // 渲染消息内容
  const renderContent = () => {
    // 主体内容现在用 renderSummary 渲染，普通消息仍用 Markdown
    if (type === 'system') {
      return (
        <Text type="secondary" style={{ fontStyle: 'italic' }}>
          {content}
        </Text>
      );
    }
    if (unifiedStream) {
      return null;
    }
    return <MarkdownRenderer content={content} />;
  };

  // 渲染元数据
  const renderMetadata = () => {
    if (!metadata) return null;

    const planTitle = metadata.plan_title;
    const planId = metadata.plan_id;
    if (!planTitle && (planId === undefined || planId === null)) {
      return null;
    }

    return (
      <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
        <div>
          关联计划:
          {planTitle ? ` ${planTitle}` : ''}
          {planId !== undefined && planId !== null ? ` (#${planId})` : ''}
        </div>
      </div>
    );
  };

  const renderActionSummary = () => {
    const summaryItems = Array.isArray(metadata?.actions_summary)
      ? (metadata?.actions_summary as Array<Record<string, any>>)
      : [];
    if (!summaryItems.length) {
      return null;
    }
    return (
      <div style={{ marginTop: 10 }}>
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text strong style={{ color: 'var(--text-primary)', fontSize: 12 }}>动作摘要</Text>
          <div>
            {summaryItems.map((item, index) => {
              const order = typeof item.order === 'number' ? item.order : index + 1;
              const success = item.success;
              const icon = success === true ? '✅' : success === false ? '⚠️' : '⏳';
              const kind = typeof item.kind === 'string' ? item.kind : 'action';
              const name = typeof item.name === 'string' && item.name ? `/${item.name}` : '';
              const messageText =
                typeof item.message === 'string' && item.message.trim().length > 0
                  ? ` - ${item.message}`
                  : '';
              return (
                <div key={`${order}_${kind}_${name}`} style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 2 }}>
                  <Text>
                    {icon} 步骤 {order}: {kind}
                    {name}
                    {messageText}
                  </Text>
                </div>
              );
            })}
          </div>
        </Space>
      </div>
    );
  };

  const renderToolStatusBar = () => {
    if (isPendingAction) {
      return null;
    }
    if (unifiedStream) {
      return null;
    }
    if (!toolResults.length) {
      return null;
    }

    const successCount = toolResults.filter((item) => item.result?.success !== false).length;
    const failCount = toolResults.length - successCount;
    const statusTag = failCount > 0 ? (
      <Tag color="red">部分失败</Tag>
    ) : (
      <Tag color="green">全部成功</Tag>
    );

    const toolTags = toolResults.slice(0, 3).map((item, index) => (
      <Tag key={`${item.name ?? 'tool'}_${index}`} color="blue">
        {item.name ?? 'tool'}
      </Tag>
    ));

    return (
      <div
        style={{
          marginTop: 10,
          padding: '8px 12px',
          borderRadius: 'var(--radius-sm)',
          background: 'var(--bg-tertiary)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          flexWrap: 'wrap',
          fontSize: 11,
        }}
      >
        <Space size={6} wrap align="center">
          <Text strong style={{ fontSize: 11 }}>工具</Text>
          {statusTag}
          <Space size={[4, 4]} wrap>
            {toolTags}
            {toolResults.length > 3 && (
              <Text type="secondary">+{toolResults.length - 3} 更多</Text>
            )}
          </Space>
          <Text type="secondary">
            {failCount > 0 ? `失败 ${failCount} · 成功 ${successCount}` : `成功 ${successCount}`}
          </Text>
        </Space>
        <Button type="link" size="small" onClick={() => setToolDrawerOpen(true)} style={{ padding: 0 }}>
          查看调用流程
        </Button>
      </div>
    );
  };

  const renderToolDrawer = () => {
    if (!toolResults.length) return null;
    if (unifiedStream) return null;
    return (
      <Drawer
        title="工具调用详情"
        placement="right"
        width={640}
        open={toolDrawerOpen}
        onClose={() => setToolDrawerOpen(false)}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {renderActionSummary()}
          {toolResults.map((result, index) => (
            <ToolResultCard
              key={`${result.name ?? 'tool'}_${index}`}
              payload={result}
              defaultOpen={false}
            />
          ))}
        </Space>
      </Drawer>
    );
  };

  const renderJobLogPanel = () => {
    if (!metadata || metadata.type !== 'job_log') {
      return null;
    }
    if (unifiedStream) {
      return null;
    }
    const jobMetadata = (metadata.job as DecompositionJobStatus | null) ?? null;
    const jobId: string | undefined = metadata.job_id ?? jobMetadata?.job_id;
    if (!jobId) {
      return null;
    }
    return (
      <JobLogPanel
        jobId={jobId}
        initialJob={jobMetadata}
        targetTaskName={metadata.target_task_name ?? null}
        planId={metadata.plan_id ?? null}
        jobType={metadata.job_type ?? jobMetadata?.job_type ?? null}
      />
    );
  };

  return (
    <div className={`message ${type}`}>
      <div className="message-content">
        {type === 'assistant' && renderAvatar()}

        <div className="message-bubble">
          {(() => {
            // 统一摘要块，在非 unifiedStream 时也优先使用
            const summaryBlock = renderSummary();
            if (unifiedStream) {
              return (
                <>
                  {renderToolProgress()}
                  {/* Thinking Process */}
                  {message.thinking_process && (
                    <ThinkingProcess
                      process={message.thinking_process}
                      isFinished={message.metadata?.status === 'completed' || message.metadata?.status === 'failed'}
                    />
                  )}
                  {summaryBlock}
                </>
              );
            }
            return (
              <>
                {/* Thinking Process - always render if present */}
                {message.thinking_process && (
                  <ThinkingProcess
                    process={message.thinking_process}
                    isFinished={message.metadata?.status === 'completed' || message.metadata?.status === 'failed'}
                  />
                )}
                {summaryBlock ?? renderContent()}
                {renderPendingActions()}
                {!isPendingAction && renderUnifiedStatusLine()}
                {!isPendingAction && renderToolStatusBar()}
                {hasFooterDivider && !isPendingAction && <Divider style={{ margin: '12px 0' }} dashed />}
                {!isPendingAction && renderJobLogPanel()}
                {renderMetadata()}
              </>
            );
          })()}
        </div>
      </div>

      <div className="message-time">
        <Space size="small">
          <Text type="secondary" style={{ fontSize: 12 }}>
            {formatTime(timestamp)}
          </Text>

          {type !== 'system' && (
            <Space size={4}>
              <Tooltip title="复制">
                <Button
                  type="text"
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={handleCopy}
                  style={{ fontSize: 10, padding: '0 4px' }}
                />
              </Tooltip>

              <Tooltip title="保存为记忆">
                <Button
                  type="text"
                  size="small"
                  icon={<DatabaseOutlined />}
                  onClick={handleSaveAsMemory}
                  loading={isSaving}
                  style={{ fontSize: 10, padding: '0 4px' }}
                />
              </Tooltip>

              {type === 'assistant' && (
                <Tooltip title="重新生成">
                  <Button
                    type="text"
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={() => {
                      // Deep Think 消息直接重新发送原始消息，不 replay actions
                      // 增加内容检测作为兜底，防止旧消息没有 metadata 导致误触发
                      // 使用正则检测 'Thinking Summary'，忽略大小写
                      const isDeepThink = (metadata as any)?.deep_think === true || /thinking\s*summary/i.test(content || '');
                      if (isDeepThink) {
                        void retryLastMessage();
                        return;
                      }

                      const trackingId = (metadata as any)?.tracking_id;
                      if (typeof trackingId === 'string' && trackingId) {
                        void retryActionRun(trackingId, ((metadata as any)?.raw_actions as any[]) ?? []);
                      } else {
                        void retryLastMessage();
                      }
                    }}
                    disabled={isProcessing}
                    style={{ fontSize: 10, padding: '0 4px' }}
                  />
                </Tooltip>
              )}
            </Space>
          )}
        </Space>
      </div>
      {!isPendingAction && renderToolDrawer()}
    </div>
  );
};

export default React.memo(ChatMessage);
