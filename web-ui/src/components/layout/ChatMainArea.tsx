import React, { useRef, useEffect } from 'react';
import { Input, Button, Space, Typography, Avatar, Divider, Empty, Alert, Tag, Tooltip, Switch } from 'antd';
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
import { SessionStorage } from '@/utils/sessionStorage';

const { TextArea } = Input;
const { Title, Text } = Typography;

const ChatMainArea: React.FC = () => {
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
    restoreSession,
    toggleMemory,
  } = useChatStore();

  const { selectedTask, currentPlan } = useTasksStore();

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 初始化会话 - 从 localStorage 恢复所有会话
  useEffect(() => {
    if (!currentSession) {
      (async () => {
        try {
          // 使用 SessionStorage 工具类恢复所有会话ID
          const allSessionIds = SessionStorage.getAllSessionIds();
          const currentSessionId = SessionStorage.getCurrentSessionId();
          
          if (allSessionIds.length > 0 && currentSessionId) {
            console.log('🔄 [ChatMainArea] 恢复所有会话:', allSessionIds);
            
            // 恢复所有会话（但不加载历史，只恢复当前会话的历史）
            for (const sessionId of allSessionIds) {
              if (sessionId !== currentSessionId) {
                // 其他会话只创建空壳，不加载历史
                const session = {
                  id: sessionId,
                  title: `对话 ${sessionId.slice(-8)}`,
                  messages: [],
                  created_at: new Date(),
                  updated_at: new Date(),
                  workflow_id: null,
                  session_id: sessionId,
                };
                // 手动添加到sessions（避免触发localStorage更新）
                const { sessions } = useChatStore.getState();
                if (!sessions.find(s => s.id === sessionId)) {
                  useChatStore.setState({ sessions: [...sessions, session] });
                }
              }
            }
            
            // 恢复当前会话并加载历史
            await restoreSession(currentSessionId, 'AI 任务编排助手');
          } else if (currentSessionId) {
            // 只有当前会话ID，恢复它
            console.log('🔄 [ChatMainArea] 恢复当前会话:', currentSessionId);
            await restoreSession(currentSessionId, 'AI 任务编排助手');
          } else {
            // 首次访问，创建新会话
            console.log('🆕 [ChatMainArea] 创建新会话');
            const session = startNewSession('AI 任务编排助手');
            SessionStorage.setCurrentSessionId(session.id);
          }
        } catch (err) {
          console.warn('[ChatMainArea] 恢复会话失败，创建新会话:', err);
          const session = startNewSession('AI 任务编排助手');
          SessionStorage.setCurrentSessionId(session.id);
        }
      })();
    }
  }, [currentSession, restoreSession, startNewSession]);

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
      background: 'white',
    }}>
      {/* 头部信息 */}
      <div style={{
        padding: '12px 20px',
        borderBottom: '1px solid #f0f0f0',
        background: 'white',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Avatar size={32} icon={<RobotOutlined />} style={{ background: '#52c41a' }} />
            <div>
              <Text strong style={{ fontSize: 16 }}>
                {currentSession?.title || 'AI 任务编排助手'}
              </Text>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 2 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {isProcessing ? '正在思考...' : '在线'}
                </Text>
                {messages.length > 0 && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    共 {messages.length} 条消息
                  </Text>
                )}
              </div>
            </div>
          </div>

          {/* 上下文信息和Memory开关 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            {(selectedTask || currentPlan || currentPlanTitle || currentTaskName) && (
              <div style={{ fontSize: 12, color: '#666', textAlign: 'right' }}>
                {(currentPlan || currentPlanTitle) && <div>当前计划: {currentPlan || currentPlanTitle}</div>}
                {(selectedTask || currentTaskName) && <div>选中任务: {selectedTask?.name || currentTaskName}</div>}
              </div>
            )}

            {/* Memory 功能开关 */}
            <Tooltip title={memoryEnabled ? "记忆增强已启用" : "记忆增强已禁用"}>
              <Space size="small">
                <DatabaseOutlined style={{ color: memoryEnabled ? '#52c41a' : '#d9d9d9', fontSize: 16 }} />
                <Switch
                  checked={memoryEnabled}
                  onChange={toggleMemory}
                  size="small"
                  checkedChildren="记忆"
                  unCheckedChildren="记忆"
                />
              </Space>
            </Tooltip>
          </div>
        </div>
      </div>

      {/* 消息区域 */}
      <div style={{
        flex: 1,
        overflow: 'auto',
        background: '#fafbfc',
      }}>
        {messages.length === 0 ? (
          renderWelcome()
        ) : (
          <div style={{
            padding: '16px 20px',
            maxWidth: 800,
            margin: '0 auto',
            width: '100%',
          }}>
            {/* 相关记忆提示 */}
            {relevantMemories.length > 0 && (
              <Alert
                message={`🧠 找到 ${relevantMemories.length} 条相关记忆`}
                description={
                  <Space wrap>
                    {relevantMemories.map(m => (
                      <Tag key={m.id} color="blue">
                        {m.keywords.slice(0, 2).join(', ')} ({(m.similarity! * 100).toFixed(0)}%)
                      </Tag>
                    ))}
                  </Space>
                }
                type="info"
                closable
                style={{ marginBottom: 16 }}
                onClose={() => useChatStore.getState().setRelevantMemories([])}
              />
            )}

            {messages.map((message) => (
              <div key={message.id} style={{ marginBottom: 16 }}>
                <ChatMessage message={message} />
              </div>
            ))}
            
            {/* 正在处理指示器 */}
            {isProcessing && (
              <div style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                marginBottom: 16,
              }}>
                <Avatar 
                  size={32} 
                  icon={<RobotOutlined />} 
                  style={{ background: '#52c41a' }} 
                />
                <div style={{
                  background: 'white',
                  padding: '12px 16px',
                  borderRadius: '12px 12px 12px 4px',
                  border: '1px solid #e5e7eb',
                  boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div className="typing-indicator">
                      <span></span>
                      <span></span>
                      <span></span>
                    </div>
                    <Text type="secondary">正在思考中...</Text>
                  </div>
                </div>
              </div>
            )}
            
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* 输入区域 */}
      <div style={{
        padding: '12px 20px',
        borderTop: '1px solid #f0f0f0',
        background: 'white',
        flexShrink: 0,
      }}>
        <div style={{ maxWidth: 800, margin: '0 auto' }}>
          <Space.Compact style={{ width: '100%' }}>
            <TextArea
              ref={inputRef}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="输入你的需求... (Shift+Enter换行，Enter发送)"
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={isProcessing}
              style={{
                resize: 'none',
                borderRadius: '12px 0 0 12px',
                fontSize: 14,
              }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSendMessage}
              disabled={!inputText.trim() || isProcessing}
              loading={isProcessing}
              style={{
                height: 'auto',
                borderRadius: '0 12px 12px 0',
                paddingLeft: 16,
                paddingRight: 16,
              }}
            >
              发送
            </Button>
          </Space.Compact>
        </div>
      </div>
    </div>
  );
};

export default ChatMainArea;
