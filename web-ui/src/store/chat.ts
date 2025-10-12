import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { ChatMessage, ChatSession } from '../types/index';
import { useTasksStore } from '@store/tasks';
import { analyzeUserIntent, executeToolBasedOnIntent } from '../services/intentAnalysis';

interface ChatState {
  // 聊天数据
  currentSession: ChatSession | null;
  sessions: ChatSession[];
  messages: ChatMessage[];
  currentWorkflowId: string | null;

  // 当前上下文
  currentPlanTitle: string | null;
  currentTaskId: number | null;
  currentTaskName: string | null;
  
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

  // 上下文操作
  setChatContext: (context: { planTitle?: string | null; taskId?: number | null; taskName?: string | null }) => void;
  clearChatContext: () => void;
  setCurrentWorkflowId: (workflowId: string | null) => void;
  
  // 快捷操作
  sendMessage: (content: string, metadata?: ChatMessage['metadata']) => Promise<void>;
  retryLastMessage: () => Promise<void>;
  startNewSession: (title?: string) => ChatSession;
  restoreSession: (sessionId: string, title?: string) => Promise<ChatSession>;
  loadChatHistory: (sessionId: string) => Promise<void>;
}

export const useChatStore = create<ChatState>()(
  subscribeWithSelector((set, get) => ({
    // 初始状态
    currentSession: null,
    sessions: [],
    messages: [],
    currentWorkflowId: null,
    currentPlanTitle: null,
    currentTaskId: null,
    currentTaskName: null,
    inputText: '',
    isTyping: false,
    isProcessing: false,
    chatPanelVisible: true,
    chatPanelWidth: 400,

    // 设置当前会话
    setCurrentSession: (session) => {
      const state = get();
      const currentId = state.currentSession?.id;
      if ((session?.id || null) === (currentId || null)) {
        return;
      }

      // 合并所有状态更新为单次set调用，避免多次重渲染
      set({
        currentSession: session,
        currentWorkflowId: session?.workflow_id ?? null,
        messages: session ? session.messages : [],
        currentPlanTitle: null,
        currentTaskId: null,
        currentTaskName: null,
      });
      
      // 更新 localStorage 中的当前会话ID
      if (session) {
        try {
          localStorage.setItem('current_session_id', session.id);
        } catch {}
      }
    },

    // 添加会话
    addSession: (session) => {
      set((state) => {
        const newSessions = [...state.sessions, session];
        // 更新 localStorage 中的所有会话ID列表
        try {
          const allSessionIds = newSessions.map(s => s.id);
          localStorage.setItem('all_session_ids', JSON.stringify(allSessionIds));
        } catch {}
        return { sessions: newSessions };
      });
    },

    // 删除会话
    removeSession: (sessionId) => {
      set((state) => {
        const newSessions = state.sessions.filter(s => s.id !== sessionId);
        // 更新 localStorage
        try {
          const allSessionIds = newSessions.map(s => s.id);
          localStorage.setItem('all_session_ids', JSON.stringify(allSessionIds));
          // 如果删除的是当前会话，清除current_session_id
          if (state.currentSession?.id === sessionId) {
            localStorage.removeItem('current_session_id');
          }
        } catch {}
        return {
          sessions: newSessions,
          currentSession: state.currentSession?.id === sessionId ? null : state.currentSession,
          messages: state.currentSession?.id === sessionId ? [] : state.messages,
        };
      });
    },

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

    // 设置聊天上下文
    setChatContext: ({ planTitle, taskId, taskName }) => {
      const state = get();
      const nextPlanTitle = planTitle !== undefined ? planTitle : state.currentPlanTitle;
      const nextTaskId = taskId !== undefined ? taskId : state.currentTaskId;
      const nextTaskName = taskName !== undefined ? taskName : state.currentTaskName;

      if (
        state.currentPlanTitle === nextPlanTitle &&
        state.currentTaskId === nextTaskId &&
        state.currentTaskName === nextTaskName
      ) {
        return;
      }

      set({
        currentPlanTitle: nextPlanTitle ?? null,
        currentTaskId: nextTaskId ?? null,
        currentTaskName: nextTaskName ?? null,
      });
    },

    clearChatContext: () => set({ currentPlanTitle: null, currentTaskId: null, currentTaskName: null }),

    setCurrentWorkflowId: (workflowId) => {
      const state = get();
      if (state.currentWorkflowId === workflowId) {
        return;
      }

      const currentSession = state.currentSession
        ? { ...state.currentSession, workflow_id: workflowId ?? undefined }
        : null;
      const sessions = state.sessions.map((session) =>
        session.id === currentSession?.id
          ? { ...session, workflow_id: workflowId ?? undefined }
          : session
      );

      try {
        const { setCurrentWorkflowId } = useTasksStore.getState();
        setCurrentWorkflowId(workflowId ?? null);
      } catch (err) {
        console.warn('Unable to sync workflow id to tasks store:', err);
      }

      set({
        currentWorkflowId: workflowId ?? null,
        currentSession,
        sessions,
      });
    },

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
      const { currentPlanTitle, currentTaskId, currentTaskName, currentWorkflowId, currentSession } = get();
      const mergedMetadata = {
        ...metadata,
        plan_title: metadata?.plan_title ?? currentPlanTitle ?? undefined,
        task_id: metadata?.task_id ?? currentTaskId ?? undefined,
        task_name: metadata?.task_name ?? currentTaskName ?? undefined,
        workflow_id: metadata?.workflow_id ?? currentWorkflowId ?? undefined,
      };
      
      // 创建用户消息
      const userMessage: ChatMessage = {
        id: `msg_${Date.now()}_user`,
        type: 'user',
        content,
        timestamp: new Date(),
        metadata: mergedMetadata,
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
        
        // 🎯 方案B2: 所有请求直接走后端chat端点
        // 后端有完整的智能路由系统（_should_create_new_workflow）
        // 可以正确处理：创建、拆分、执行、普通对话
        // 前端意图分析已禁用，避免逻辑重复和不一致
        
        console.log('🎯 所有请求统一走后端智能路由');

        const chatRequest = {
          task_id: mergedMetadata.task_id,
          plan_title: mergedMetadata.plan_title,
          workflow_id: mergedMetadata.workflow_id,
          session_id: currentSession?.session_id,
          history: recentMessages,
          mode: 'assistant' as const
        };
        console.log('📤 发送聊天请求:', chatRequest);
        
        const result = await chatApi.sendMessage(content, chatRequest);
        console.log('🎯 Chat result:', result);
        
        // 处理特殊操作
        let finalContent = result.response;
        
        // 检查是否为Agent工作流程响应
        if (result.metadata?.agent_workflow) {
          console.log('🤖 检测到Agent工作流程响应:', result.metadata);
          
          // 触发DAG更新事件
          window.dispatchEvent(new CustomEvent('tasksUpdated', { 
            detail: { 
              type: 'agent_workflow_created',
              workflow_id: result.metadata.workflow_id,
              total_tasks: result.metadata.total_tasks,
              dag_structure: result.metadata.dag_structure
            }
          }));
          
          console.log('✅ Agent工作流程创建成功，已通知DAG组件刷新');

          if (result.metadata.workflow_id) {
            const workflowId = result.metadata.workflow_id;
            get().setCurrentWorkflowId(workflowId);
          }
          // 同步后端返回的 session_id 到当前会话（用于前端按会话过滤任务）
          if (result.metadata?.session_id) {
            const state = get();
            const newSessionId = result.metadata.session_id as string;
            const current = state.currentSession
              ? { ...state.currentSession, session_id: newSessionId }
              : null;
            const sessions = state.sessions.map((s) =>
              s.id === current?.id ? { ...s, session_id: newSessionId } : s
            );
            set({ currentSession: current, sessions });
          }
        }
        
        // 如果AI建议创建计划，尝试执行（兼容旧版本）
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
                
               // 触发全局状态更新，让DAG组件知道需要刷新
               console.log('✅ 计划创建成功，触发任务数据刷新...');
               // 使用事件总线通知DAG组件刷新
               window.dispatchEvent(new CustomEvent('tasksUpdated', { 
                 detail: { 
                   type: 'plan_created',
                   planTitle: planResult.title,
                   tasksCount: planResult.tasks?.length || 0
                 }
               }));
                set({ currentPlanTitle: planResult.title, currentTaskId: null, currentTaskName: null });
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
            actions: result.actions,
            plan_title: result.metadata?.plan_title || mergedMetadata.plan_title,
            task_id: result.metadata?.task_id || mergedMetadata.task_id,
          }
        };
        
        get().addMessage(assistantMessage);
        set({ isProcessing: false });

        // 如果响应中带有新的上下文，更新状态
        if (result.metadata?.plan_title) {
          set({ currentPlanTitle: result.metadata.plan_title });
        }
        if (result.metadata?.task_id) {
          set({ currentTaskId: result.metadata.task_id });
        }
        if (result.metadata?.workflow_id) {
          get().setCurrentWorkflowId(result.metadata.workflow_id);
        }
        // 捕获并写入 session_id，确保后续任务过滤能匹配到当前对话
        if (result.metadata?.session_id) {
          const state = get();
          const newSessionId = result.metadata.session_id as string;
          const current = state.currentSession
            ? { ...state.currentSession, session_id: newSessionId }
            : null;
          const sessions = state.sessions.map((s) =>
            s.id === current?.id ? { ...s, session_id: newSessionId } : s
          );
          set({ currentSession: current, sessions });
          try {
            localStorage.setItem('current_session_id', newSessionId);
          } catch {}
        }

        // 无论是否携带metadata，统一派发一次刷新事件，驱动DAG重新加载
        try {
          const { currentSession: cs, currentWorkflowId: cw } = get();
          window.dispatchEvent(new CustomEvent('tasksUpdated', {
            detail: {
              type: 'chat_message_processed',
              session_id: cs?.session_id ?? null,
              workflow_id: cw ?? null,
            }
          }));
        } catch (e) {
          console.warn('Failed to dispatch tasksUpdated event:', e);
        }
      
      } catch (error) {
        console.error('Failed to send message:', error);
        set({ isProcessing: false });
        
        // 如果API失败，提供友好的错误信息
        const errorMessage: ChatMessage = {
          id: `msg_${Date.now()}_assistant`,
          type: 'assistant',
          content: '抱歉，我暂时无法处理你的请求。可能的原因：\n\n1. 后端服务未完全启动\n2. LLM API未配置\n3. 网络连接问题\n\n请检查后端服务状态，或稍后重试。',
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

    // 开始新会话（总是生成新的ID）
    startNewSession: (title) => {
      const sessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
      const session: ChatSession = {
        id: sessionId,
        title: title || `对话 ${new Date().toLocaleString()}`,
        messages: [],
        created_at: new Date(),
        updated_at: new Date(),
        workflow_id: null,
        session_id: sessionId,
      };

      console.log('🆕 创建新会话:', {
        前端会话ID: session.id,
        后端会话ID: session.session_id,
        标题: session.title
      });

      get().addSession(session);
      get().setCurrentSession(session);
      set({ currentWorkflowId: null });
      
      // 保存当前会话ID和所有会话ID列表
      try {
        localStorage.setItem('current_session_id', sessionId);
        const allSessionIds = get().sessions.map(s => s.id);
        localStorage.setItem('all_session_ids', JSON.stringify(allSessionIds));
      } catch {}
      
      return session;
    },

    // 恢复已有会话（用于刷新后保持历史）
    restoreSession: async (sessionId, title) => {
      const state = get();
      let session = state.sessions.find((s) => s.id === sessionId) || null;

      if (!session) {
        session = {
          id: sessionId,
          title: title || `对话 ${new Date().toLocaleString()}`,
          messages: [],
          created_at: new Date(),
          updated_at: new Date(),
          workflow_id: null,
          session_id: sessionId,
        };
        get().addSession(session);
      }

      set({
        currentSession: session,
        currentWorkflowId: null,
      });

      try { localStorage.setItem('current_session_id', sessionId); } catch {}

      await get().loadChatHistory(sessionId);

      const updatedMessages = get().messages;
      if (updatedMessages.length > 0) {
        const refreshed = {
          ...session,
          messages: updatedMessages,
          updated_at: new Date(),
        };
        set((currentState) => ({
          currentSession: refreshed,
          sessions: currentState.sessions.some((s) => s.id === refreshed.id)
            ? currentState.sessions.map((s) => (s.id === refreshed.id ? refreshed : s))
            : [...currentState.sessions, refreshed],
        }));
        return refreshed;
      }

      return get().currentSession || session;
    },

    // 加载聊天历史
    loadChatHistory: async (sessionId: string) => {
      try {
        console.log('📖 加载聊天历史:', sessionId);
        const response = await fetch(`http://127.0.0.1:8000/chat/history/${sessionId}?limit=100`);
        
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.success && data.messages && data.messages.length > 0) {
          console.log(`✅ 加载了 ${data.messages.length} 条历史消息`);
          
          // 转换后端消息格式为前端格式
          const messages: ChatMessage[] = data.messages.map((msg: any, index: number) => ({
            id: `${sessionId}_${index}`,
            type: (msg.role || 'assistant') as 'user' | 'assistant' | 'system',
            content: msg.content,
            timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(),
            metadata: {},
          }));
          
          // 更新消息列表
          set({ messages });
          
          // 更新对应会话的消息（无论是否为当前会话）
          const state = get();
          const targetSession = state.sessions.find(s => s.id === sessionId);
          
          if (targetSession) {
            const updatedSession = {
              ...targetSession,
              messages,
              updated_at: new Date(),
            };
            
            // 更新 sessions 数组
            const updatedSessions = state.sessions.map(s => 
              s.id === sessionId ? updatedSession : s
            );
            
            // 如果是当前会话，也更新 currentSession
            const updatedCurrentSession = state.currentSession?.id === sessionId
              ? updatedSession
              : state.currentSession;
            
            set({
              sessions: updatedSessions,
              currentSession: updatedCurrentSession,
            });
          }
        } else {
          console.log('📭 没有历史消息');
        }
      } catch (error) {
        console.error('加载聊天历史失败:', error);
        throw error;
      }
    },
  }))
);
