import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { ChatMessage, ChatSession } from '../types/index';

interface ChatState {
  // 聊天数据
  currentSession: ChatSession | null;
  sessions: ChatSession[];
  messages: ChatMessage[];
  
  // 输入状态
  inputText: string;
  isTyping: boolean;
  isProcessing: boolean;
  
  // UI状态
  chatPanelVisible: boolean;
  chatPanelWidth: number;
  
  // 操作方法
  setCurrentSession: (session: ChatSession | null) => void;
  addSession: (session: ChatSession) => void;
  removeSession: (sessionId: string) => void;
  addMessage: (message: ChatMessage) => void;
  updateMessage: (messageId: string, updates: Partial<ChatMessage>) => void;
  removeMessage: (messageId: string) => void;
  clearMessages: () => void;
  
  // 输入操作
  setInputText: (text: string) => void;
  setIsTyping: (typing: boolean) => void;
  setIsProcessing: (processing: boolean) => void;
  
  // UI操作
  toggleChatPanel: () => void;
  setChatPanelVisible: (visible: boolean) => void;
  setChatPanelWidth: (width: number) => void;
  
  // 快捷操作
  sendMessage: (content: string, metadata?: ChatMessage['metadata']) => Promise<void>;
  retryLastMessage: () => Promise<void>;
  startNewSession: (title?: string) => ChatSession;
}

export const useChatStore = create<ChatState>()(
  subscribeWithSelector((set, get) => ({
    // 初始状态
    currentSession: null,
    sessions: [],
    messages: [],
    inputText: '',
    isTyping: false,
    isProcessing: false,
    chatPanelVisible: true,
    chatPanelWidth: 400,

    // 设置当前会话
    setCurrentSession: (session) => {
      set({ currentSession: session });
      if (session) {
        set({ messages: session.messages });
      } else {
        set({ messages: [] });
      }
    },

    // 添加会话
    addSession: (session) => set((state) => ({
      sessions: [...state.sessions, session],
    })),

    // 删除会话
    removeSession: (sessionId) => set((state) => ({
      sessions: state.sessions.filter(s => s.id !== sessionId),
      currentSession: state.currentSession?.id === sessionId ? null : state.currentSession,
      messages: state.currentSession?.id === sessionId ? [] : state.messages,
    })),

    // 添加消息
    addMessage: (message) => set((state) => {
      const newMessages = [...state.messages, message];
      
      // 更新当前会话
      let updatedSession = state.currentSession;
      if (updatedSession) {
        updatedSession = {
          ...updatedSession,
          messages: newMessages,
          updated_at: new Date(),
        };
      }

      // 更新会话列表
      const updatedSessions = state.sessions.map(session =>
        session.id === updatedSession?.id ? updatedSession : session
      );

      return {
        messages: newMessages,
        currentSession: updatedSession,
        sessions: updatedSessions,
      };
    }),

    // 更新消息
    updateMessage: (messageId, updates) => set((state) => {
      const updatedMessages = state.messages.map(msg =>
        msg.id === messageId ? { ...msg, ...updates } : msg
      );

      // 更新当前会话
      let updatedSession = state.currentSession;
      if (updatedSession) {
        updatedSession = {
          ...updatedSession,
          messages: updatedMessages,
          updated_at: new Date(),
        };
      }

      return {
        messages: updatedMessages,
        currentSession: updatedSession,
      };
    }),

    // 删除消息
    removeMessage: (messageId) => set((state) => ({
      messages: state.messages.filter(msg => msg.id !== messageId),
    })),

    // 清空消息
    clearMessages: () => set({ messages: [] }),

    // 设置输入文本
    setInputText: (text) => set({ inputText: text }),

    // 设置正在输入状态
    setIsTyping: (typing) => set({ isTyping: typing }),

    // 设置处理中状态
    setIsProcessing: (processing) => set({ isProcessing: processing }),

    // 切换聊天面板显示
    toggleChatPanel: () => set((state) => ({
      chatPanelVisible: !state.chatPanelVisible,
    })),

    // 设置聊天面板显示
    setChatPanelVisible: (visible) => set({ chatPanelVisible: visible }),

    // 设置聊天面板宽度
    setChatPanelWidth: (width) => set({ chatPanelWidth: width }),

    // 发送消息
    sendMessage: async (content, metadata) => {
      
      // 创建用户消息
      const userMessage: ChatMessage = {
        id: `msg_${Date.now()}_user`,
        type: 'user',
        content,
        timestamp: new Date(),
        metadata,
      };

      // 添加用户消息
      get().addMessage(userMessage);
      
      // 设置处理中状态
      set({ isProcessing: true, inputText: '' });

      try {
        // 使用真实的聊天API进行对话
        console.log('🚀 开始聊天...', { content });
        
        const { chatApi } = await import('@api/chat');
        console.log('💬 Chat API loaded successfully');
        
        // 获取对话历史
        const messages = get().messages;
        const recentMessages = messages.slice(-10).map(msg => ({
          role: msg.type,
          content: msg.content,
          timestamp: msg.timestamp.toISOString()
        }));
        
        const chatRequest = {
          task_id: metadata?.task_id,
          plan_title: metadata?.plan_title,
          history: recentMessages,
          mode: 'assistant' as const
        };
        console.log('📤 发送聊天请求:', chatRequest);
        
        const result = await chatApi.sendMessage(content, chatRequest);
        console.log('🎯 Chat result:', result);
        
        // 处理特殊操作
        let finalContent = result.response;
        
        // 如果AI建议创建计划，尝试执行
        if (result.actions && result.actions.length > 0) {
          for (const action of result.actions) {
            if (action.type === 'suggest_plan_creation') {
              console.log('🎯 AI建议创建计划，尝试执行...');
              try {
                const { plansApi } = await import('@api/plans');
                const planResult = await plansApi.proposePlan({
                  goal: content,
                  title: `AI生成计划_${new Date().getTime()}`,
                });
                
                // 添加计划创建结果到回复中
                finalContent += `\n\n🎉 **我已经为你创建了计划！**\n\n📋 **计划标题**: ${planResult.title}\n📝 **任务数量**: ${planResult.tasks?.length || 0}个\n\n💡 你可以说"查看计划详情"了解更多信息。`;
              } catch (planError) {
                console.error('自动创建计划失败:', planError);
                finalContent += '\n\n💡 我可以帮你创建详细的任务计划，请描述具体的目标。';
              }
            }
          }
        }

        const assistantMessage: ChatMessage = {
          id: `msg_${Date.now()}_assistant`,
          type: 'assistant',
          content: finalContent,
          timestamp: new Date(),
          metadata: {
            actions: result.actions
          }
        };
        
        get().addMessage(assistantMessage);
        set({ isProcessing: false });
        
      } catch (error) {
        console.error('Failed to send message:', error);
        set({ isProcessing: false });
        
        // 如果API失败，提供友好的错误信息
        const errorMessage: ChatMessage = {
          id: `msg_${Date.now()}_assistant`,
          type: 'assistant',
          content: '抱歉，我暂时无法处理你的请求。可能的原因：\n\n1. 后端服务未完全启动\n2. GLM API未配置\n3. 网络连接问题\n\n请检查后端服务状态，或稍后重试。',
          timestamp: new Date(),
        };
        get().addMessage(errorMessage);
      }
    },

    // 重试最后一条消息
    retryLastMessage: async () => {
      const { messages } = get();
      const lastUserMessage = [...messages].reverse().find(msg => msg.type === 'user');
      
      if (lastUserMessage) {
        await get().sendMessage(lastUserMessage.content, lastUserMessage.metadata);
      }
    },

    // 开始新会话
    startNewSession: (title) => {
      const session: ChatSession = {
        id: `session_${Date.now()}`,
        title: title || `对话 ${new Date().toLocaleString()}`,
        messages: [],
        created_at: new Date(),
        updated_at: new Date(),
      };

      get().addSession(session);
      get().setCurrentSession(session);
      
      return session;
    },
  }))
);
