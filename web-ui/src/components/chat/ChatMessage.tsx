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
import ReactMarkdown from 'markdown-to-jsx';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ToolResultCard from './ToolResultCard';
import JobLogPanel from './JobLogPanel';
import type { DecompositionJobStatus } from '@/types';

const { Text } = Typography;

interface ChatMessageProps {
  message: ChatMessageType;
}

const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const { type, content, timestamp, metadata } = message;
  const { saveMessageAsMemory } = useChatStore();
  const [isSaving, setIsSaving] = useState(false);
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false);
  const [pendingDetailOpen, setPendingDetailOpen] = useState(false);

  const toolResults: ToolResultPayload[] = useMemo(
    () =>
      Array.isArray(metadata?.tool_results)
        ? (metadata?.tool_results as ToolResultPayload[])
        : [],
    [metadata?.tool_results],
  );
  const hasFooterDivider =
    toolResults.length > 0 ||
    (!!metadata && (metadata.type === 'job_log' || metadata.plan_id !== undefined || metadata.plan_title));
  const isPendingAction =
    type === 'assistant' &&
    metadata?.status === 'pending' &&
    Array.isArray(metadata?.raw_actions) &&
    metadata.raw_actions.length > 0;

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

  // 渲染头像
  const renderAvatar = () => {
    const avatarProps = {
      size: 32 as const,
      style: { flexShrink: 0 },
    };

    switch (type) {
      case 'user':
        return (
          <Avatar 
            {...avatarProps}
            icon={<UserOutlined />}
            style={{ ...avatarProps.style, backgroundColor: '#1890ff' }}
          />
        );
      case 'assistant':
        return (
          <Avatar 
            {...avatarProps}
            icon={<RobotOutlined />}
            style={{ ...avatarProps.style, backgroundColor: '#52c41a' }}
          />
        );
      case 'system':
        return (
          <Avatar 
            {...avatarProps}
            icon={<InfoCircleOutlined />}
            style={{ ...avatarProps.style, backgroundColor: '#faad14' }}
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
    const actions = Array.isArray(metadata?.raw_actions) ? metadata?.raw_actions : [];
    const visible = pendingDetailOpen ? actions : actions.slice(0, 3);

    return (
      <div
        style={{
          marginTop: 8,
          padding: '12px 12px',
          borderRadius: 8,
          border: '1px dashed #d9d9d9',
          background: '#fafafa',
          opacity: 0.85,
        }}
      >
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Space align="center" size={8}>
            <div
              style={{
                width: 8,
                height: 8,
                borderRadius: 10,
                background: '#1890ff',
                boxShadow: '0 0 0 6px rgba(24,144,255,0.15)',
              }}
            />
            <Text type="secondary" italic style={{ opacity: 0.9 }}>
              正在规划并准备调用 {actions.length} 个工具（后台执行中）…
            </Text>
          </Space>
          <div style={{ fontSize: 12, color: '#666' }}>
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

  // 渲染消息内容
  const renderContent = () => {
    if (type === 'system') {
      return (
        <Text type="secondary" style={{ fontStyle: 'italic' }}>
          {content}
        </Text>
      );
    }

    // Pending 工具规划，采用幽灵样式提示，不展示正文内容
    if (isPendingAction) {
      return renderPendingActions();
    }

    return (
      <ReactMarkdown
        options={{
          overrides: {
            code: {
              component: CodeBlock,
            },
            pre: {
              component: ({ children }: { children: React.ReactNode }) => (
                <div>{children}</div>
              ),
            },
            p: {
              props: {
                style: { margin: '0 0 8px 0', lineHeight: 1.6 },
              },
            },
            ul: {
              props: {
                style: { margin: '8px 0', paddingLeft: '20px' },
              },
            },
            ol: {
              props: {
                style: { margin: '8px 0', paddingLeft: '20px' },
              },
            },
            blockquote: {
              props: {
                style: {
                  borderLeft: '4px solid #d9d9d9',
                  paddingLeft: '12px',
                  margin: '8px 0',
                  color: '#666',
                  fontStyle: 'italic',
                },
              },
            },
          },
        }}
      >
        {content}
      </ReactMarkdown>
    );
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
      <div style={{ marginTop: 12 }}>
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Text strong>动作摘要</Text>
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
                <div key={`${order}_${kind}_${name}`} style={{ fontSize: 12, color: '#555', marginBottom: 4 }}>
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
          marginTop: 12,
          padding: '8px 12px',
          borderRadius: 8,
          background: '#f5f5f5',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <Space size={8} wrap align="center">
          <Text strong>工具调用</Text>
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
              defaultOpen={index === 0}
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
      {renderAvatar()}
      
      <div className="message-content">
        <div className="message-bubble">
          {renderContent()}
          {!isPendingAction && renderToolStatusBar()}
          {hasFooterDivider && !isPendingAction && <Divider style={{ margin: '12px 0' }} dashed />}
          {!isPendingAction && renderJobLogPanel()}
          {renderMetadata()}
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
                      style={{ fontSize: 10, padding: '0 4px' }}
                    />
                  </Tooltip>
                )}
              </Space>
            )}
          </Space>
        </div>
      </div>
      {!isPendingAction && renderToolDrawer()}
    </div>
  );
};

export default ChatMessage;
