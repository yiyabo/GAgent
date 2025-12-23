import React, { useRef, useEffect } from 'react';
import {
  App as AntdApp,
  Input,
  Button,
  Space,
  Typography,
  Avatar,
  Divider,
  Empty,
  Alert,
  Tag,
  Tooltip,
  Switch,
  Select,
} from 'antd';
import {
  SendOutlined,
  PaperClipOutlined,
  RobotOutlined,
  UserOutlined,
  MessageOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import ChatMessage from '@components/chat/ChatMessage';
import FileUploadButton from '@components/chat/FileUploadButton';
import UploadedFilesList from '@components/chat/UploadedFilesList';

const { TextArea } = Input;
const { Title, Text } = Typography;

const ChatMainArea: React.FC = () => {
  const { message } = AntdApp.useApp();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);

  const {
    messages,
    inputText,
    isProcessing,
    currentSession,
    currentPlanTitle,
    currentTaskName,
    memoryEnabled,
    relevantMemories,
    setInputText,
    sendMessage,
    startNewSession,
    loadSessions,
    loadChatHistory,
    toggleMemory,
    defaultSearchProvider,
    setDefaultSearchProvider,
    isUpdatingProvider,
    defaultBaseModel,
    setDefaultBaseModel,
    isUpdatingBaseModel,
  } = useChatStore();

  const { selectedTask, currentPlan } = useTasksStore();

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 初始化会话：优先从后端加载列表
  useEffect(() => {
    (async () => {
      if (currentSession) {
        return;
      }
      try {
        await loadSessions();
        const selected = useChatStore.getState().currentSession;
        if (selected) {
          await loadChatHistory(selected.id);
          return;
        }
        const session = startNewSession('AI 任务编排助手');
        await loadChatHistory(session.id);
      } catch (err) {
        console.warn('[ChatMainArea] 会话初始化失败，尝试创建新会话:', err);
        const session = startNewSession('AI 任务编排助手');
        await loadChatHistory(session.id);
      }
    })();
  }, [currentSession, loadSessions, loadChatHistory, startNewSession]);

  // 处理发送消息
  const handleSendMessage = async () => {
    if (!inputText.trim() || isProcessing) return;

    const metadata = {
      task_id: selectedTask?.id ?? undefined,
      plan_title: currentPlan || currentPlanTitle || undefined,
      task_name: selectedTask?.name ?? currentTaskName ?? undefined,
    };

    await sendMessage(inputText.trim(), metadata);
    inputRef.current?.focus();
  };

  const handleProviderChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultSearchProvider(
        (value as 'builtin' | 'perplexity' | 'tavily') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] 切换搜索来源失败:', err);
      message.error('切换搜索来源失败，请稍后重试。');
    }
  };

  const handleBaseModelChange = async (value: string | undefined) => {
    if (!currentSession) {
      return;
    }
    try {
      await setDefaultBaseModel(
        (value as 'qwen3-max' | 'glm-4.6' | 'kimi-k2-thinking') ?? null
      );
    } catch (err) {
      console.error('[ChatMainArea] 切换基座模型失败:', err);
      message.error('切换基座模型失败，请稍后重试。');
    }
  };

  const providerOptions = [
    { label: '模型内置搜索', value: 'builtin' },
    { label: 'Perplexity 搜索', value: 'perplexity' },
    { label: 'Tavily MCP 搜索', value: 'tavily' },
  ];

  const providerValue = defaultSearchProvider ?? undefined;
  const baseModelValue = defaultBaseModel ?? undefined;

  const baseModelOptions = [
    { label: 'Qwen3-Max', value: 'qwen3-max' },
    { label: 'GLM-4.6', value: 'glm-4.6' },
    { label: 'Kimi K2 Thinking', value: 'kimi-k2-thinking' },
  ];

  // 处理键盘事件
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // 快捷操作
  const quickActions = [
    { text: '创建新计划', action: () => setInputText('帮我创建一个新的计划') },
    { text: '查看任务状态', action: () => setInputText('显示当前所有任务的状态') },
    { text: '系统帮助', action: () => setInputText('我需要帮助，请告诉我可以做什么') },
  ];

  // 渲染欢迎界面
  const renderWelcome = () => (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100%',
      padding: '0 40px',
      textAlign: 'center',
    }}>
      <Avatar
        size={64}
        icon={<RobotOutlined />}
        style={{
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
          marginBottom: 16,
        }}
      />

      <Title level={3} style={{ marginBottom: 12, color: '#1f2937' }}>
        AI 智能任务编排助手
      </Title>

      <Text
        style={{
          fontSize: 14,
          color: '#6b7280',
          marginBottom: 24,
          lineHeight: 1.5,
        }}
      >
        我可以帮你创建计划、分解任务、执行调度，让复杂的项目变得简单高效
      </Text>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 280 }}>
        {quickActions.map((action, index) => (
          <Button
            key={index}
            size="middle"
            style={{
              height: 40,
              borderRadius: 8,
              border: '1px solid #e5e7eb',
              background: 'white',
              boxShadow: '0 1px 2px rgba(0, 0, 0, 0.05)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-start',
              paddingLeft: 16,
            }}
            onClick={action.action}
          >
            <MessageOutlined style={{ marginRight: 10, color: '#6366f1', fontSize: 14 }} />
            <span style={{ color: '#374151', fontWeight: 500, fontSize: 14 }}>{action.text}</span>
          </Button>
        ))}
      </div>

      <Divider style={{ margin: '24px 0', width: '100%' }} />

      <Text type="secondary" style={{ fontSize: 13 }}>
        💡 你可以直接输入自然语言描述你的需求，我会智能理解并帮助执行
      </Text>
    </div>
  );

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: 'var(--bg-primary)',
    }}>
      {/* 头部信息 - 极简设计 */}
      <div style={{
        padding: '12px 20px',
        borderBottom: '1px solid var(--border-color)',
        background: 'var(--bg-primary)',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Avatar
            size={28}
            icon={<RobotOutlined />}
            style={{
              background: 'var(--primary-gradient)',
              borderRadius: 6,
            }}
          />
          <div>
            <Text strong style={{ fontSize: 14, color: 'var(--text-primary)' }}>
              {currentSession?.title || 'AI 任务编排助手'}
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
              <Text type="secondary" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                在线
              </Text>
            </div>
          </div>
        </div>

        {/* 简化操作区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* 搜索提供商选择器 */}
          <Select
            size="small"
            value={providerValue}
            onChange={handleProviderChange}
            options={providerOptions}
            style={{ width: 130 }}
            placeholder="搜索来源"
            disabled={isUpdatingProvider}
          />

          <Select
            size="small"
            value={baseModelValue}
            onChange={handleBaseModelChange}
            options={baseModelOptions}
            style={{ width: 140 }}
            placeholder="基座模型"
            disabled={isUpdatingBaseModel}
          />
          
          {/* Memory 开关 */}
          <Tooltip title={memoryEnabled ? "记忆已启用" : "记忆已禁用"}>
            <Switch
              checked={memoryEnabled}
              onChange={toggleMemory}
              size="small"
              style={{
                background: memoryEnabled ? 'var(--primary-color)' : undefined,
              }}
            />
          </Tooltip>
        </div>
      </div>

      {/* 消息区域 */}
      <div style={{
        flex: 1,
        overflow: 'auto',
        background: 'var(--bg-primary)',
        padding: '16px 0',
      }}>
        {messages.length === 0 ? (
          renderWelcome()
        ) : (
          <div style={{
            padding: '0 24px',
            maxWidth: 900,
            margin: '0 auto',
            width: '100%',
          }}>
            {/* 相关记忆提示 - 极简风格 */}
            {relevantMemories.length > 0 && (
              <div style={{
                marginBottom: 16,
                padding: '10px 14px',
                background: 'var(--bg-tertiary)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 12,
                color: 'var(--text-secondary)',
              }}>
                <span style={{ color: 'var(--primary-color)' }}>🧠</span>
                {relevantMemories.length} 条相关记忆
              </div>
            )}

            {messages.map((message) => (
              <div key={message.id} style={{ marginBottom: 16 }}>
                <ChatMessage message={message} />
              </div>
            ))}
            
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* 输入区域 - Claude 风格 */}
      <div style={{
        padding: '16px 24px 20px',
        background: 'var(--bg-primary)',
        borderTop: '1px solid var(--border-color)',
        flexShrink: 0,
      }}>
        <div style={{ maxWidth: 920, margin: '0 auto' }}>
          {/* 上传文件列表 */}
          <UploadedFilesList />
          
          <div
            style={{
              display: 'flex',
              gap: 12,
              alignItems: 'stretch',
            }}
          >
            {/* 左侧上传按钮组 */}
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              justifyContent: 'center',
              background: 'var(--bg-tertiary)',
              padding: '6px 10px',
              borderRadius: 'var(--radius-md)',
            }}>
              <FileUploadButton type="file" size="small" />
              <FileUploadButton type="image" size="small" />
            </div>

            {/* 输入框 - Claude 风格 */}
            <div style={{
              flex: 1,
              background: '#FFFFFF',
              borderRadius: 'var(--radius-xl)',
              padding: '6px',
              display: 'flex',
              alignItems: 'flex-end',
              border: '2px solid var(--border-color)',
              boxShadow: '0 8px 32px -12px rgba(0, 0, 0, 0.03)',
              transition: 'var(--transition-normal)',
            }}>
              <TextArea
                ref={inputRef}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="输入你的需求，让我来帮你完成..."
                autoSize={{ minRows: 1, maxRows: 5 }}
                disabled={isProcessing}
                style={{
                  resize: 'none',
                  border: 'none',
                  background: 'transparent',
                  fontSize: 15,
                  lineHeight: 1.7,
                  letterSpacing: '0.018em',
                  outline: 'none',
                  boxShadow: 'none',
                }}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={handleSendMessage}
                disabled={!inputText.trim() || isProcessing}
                loading={isProcessing}
                style={{
                  height: 36,
                  borderRadius: 'var(--radius-md)',
                  minWidth: 80,
                  background: 'var(--primary-color)',
                  border: 'none',
                }}
              >
                发送
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatMainArea;
