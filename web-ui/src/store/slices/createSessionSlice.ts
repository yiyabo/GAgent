import { ChatSliceCreator } from './types';
import { ChatSession } from '@/types';
import { SessionStorage } from '@/utils/sessionStorage';
import { chatApi } from '@api/chat';
import { ENV } from '@/config/env';
import { useTasksStore } from '@store/tasks';
import { dispatchPlanSyncEvent } from '@utils/planSyncEvents';
import {
    autoTitleHistory,
    pendingAutotitleSessions,
    resolveHistoryCursor,
    summaryToChatSession,
} from '../chatUtils';

export const createSessionSlice: ChatSliceCreator = (set, get) => ({
    currentSession: null,
    sessions: [],

    setCurrentSession: (session) => {
        const sessionPlanId = session?.plan_id ?? null;
        const sessionPlanTitle = session?.plan_title ?? null;
        const sessionTaskId = session?.current_task_id ?? null;
        const sessionTaskName = session?.current_task_name ?? null;
        const provider = session?.defaultSearchProvider ?? null;
        const baseModel = session?.defaultBaseModel ?? null;
        const llmProvider = session?.defaultLLMProvider ?? null;
        const historyCursor = session ? resolveHistoryCursor(session.messages) : null;
        const historyHasMore = historyCursor !== null;

        set({
            currentSession: session,
            currentWorkflowId: session?.workflow_id ?? null,
            messages: session ? session.messages : [],
            currentPlanId: sessionPlanId,
            currentPlanTitle: sessionPlanTitle,
            currentTaskId: sessionTaskId,
            currentTaskName: sessionTaskName,
            defaultSearchProvider: provider,
            defaultBaseModel: baseModel,
            defaultLLMProvider: llmProvider,
            historyBeforeId: historyCursor,
            historyHasMore,
            historyLoading: false,
        });

        if (session) {
            SessionStorage.setCurrentSessionId(session.id);
        } else {
            SessionStorage.clearCurrentSessionId();
        }
    },

    addSession: (session) => {
        const normalized: ChatSession = {
            ...session,
            defaultSearchProvider: session.defaultSearchProvider ?? null,
            defaultBaseModel: session.defaultBaseModel ?? null,
            defaultLLMProvider: session.defaultLLMProvider ?? null,
        };
        set((state) => {
            const exists = state.sessions.some((s) => s.id === normalized.id);
            const newSessions = exists
                ? state.sessions.map((s) => (s.id === normalized.id ? normalized : s))
                : [...state.sessions, normalized];
            SessionStorage.setAllSessionIds(newSessions.map((s) => s.id));
            return { sessions: newSessions };
        });
    },

    removeSession: (sessionId) => {
        autoTitleHistory.delete(sessionId);
        pendingAutotitleSessions.delete(sessionId);
        set((state) => {
            const newSessions = state.sessions.filter(s => s.id !== sessionId);
            const allSessionIds = newSessions.map(s => s.id);
            SessionStorage.setAllSessionIds(allSessionIds);
            if (state.currentSession?.id === sessionId) {
                SessionStorage.clearCurrentSessionId();
            }
            return {
                sessions: newSessions,
                currentSession: state.currentSession?.id === sessionId ? null : state.currentSession,
                messages: state.currentSession?.id === sessionId ? [] : state.messages,
                defaultSearchProvider:
                    state.currentSession?.id === sessionId ? null : state.defaultSearchProvider,
                defaultBaseModel:
                    state.currentSession?.id === sessionId ? null : state.defaultBaseModel,
                defaultLLMProvider:
                    state.currentSession?.id === sessionId ? null : state.defaultLLMProvider,
            };
        });
    },

    deleteSession: async (sessionId, options) => {
        const archive = options?.archive ?? false;

        try {
            const checkResponse = await fetch(`${ENV.API_BASE_URL}/chat/sessions/${sessionId}`, {
                method: 'HEAD',
            });

            if (!checkResponse.ok) {
                console.warn(`会话 ${sessionId} 在后端不存在，从本地移除`);
                get().removeSession(sessionId);
                return;
            }

            await chatApi.deleteSession(
                sessionId,
                archive ? { archive: true } : undefined
            );
        } catch (error) {
            if (error instanceof Error && error.message.includes('404')) {
                console.warn(`会话 ${sessionId} 不存在，从本地移除`);
                get().removeSession(sessionId);
                return;
            }
            console.error('删除会话失败:', error);
            throw error;
        }

        if (archive) {
            set((state) => {
                const updatedSessions = state.sessions.map((session) =>
                    session.id === sessionId ? { ...session, is_active: false } : session
                );
                const updatedCurrent =
                    state.currentSession?.id === sessionId
                        ? { ...state.currentSession, is_active: false }
                        : state.currentSession;
                return {
                    sessions: updatedSessions,
                    currentSession: updatedCurrent,
                };
            });
            dispatchPlanSyncEvent(
                {
                    type: 'session_archived',
                    session_id: sessionId,
                    plan_id: null,
                },
                { source: 'chat.session' }
            );
            return;
        }

        const wasCurrent = get().currentSession?.id === sessionId;
        get().removeSession(sessionId);

        if (wasCurrent) {
            const tasksStore = useTasksStore.getState();
            tasksStore.setTasks([]);
            tasksStore.clearTaskResultCache();
            tasksStore.closeTaskDrawer();

            const remainingSessions = get().sessions;
            const fallbackSession =
                remainingSessions.find((session) => session.is_active) ??
                remainingSessions[0] ??
                null;

            if (fallbackSession) {
                get().setCurrentSession(fallbackSession);
                try {
                    await get().loadChatHistory(fallbackSession.id);
                } catch (historyError) {
                    console.warn('加载备用会话历史失败:', historyError);
                }
            } else {
                set({
                    currentPlanId: null,
                    currentPlanTitle: null,
                    currentTaskId: null,
                    currentTaskName: null,
                    currentWorkflowId: null,
                    messages: [],
                });
            }
        }

        dispatchPlanSyncEvent(
            {
                type: 'session_deleted',
                session_id: sessionId,
                plan_id: null,
            },
            { source: 'chat.session' }
        );
    },

    startNewSession: (title) => {
        const sessionId = `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const providerPreference = get().defaultSearchProvider ?? null;
        const baseModelPreference = get().defaultBaseModel ?? null;
        const llmProviderPreference = get().defaultLLMProvider ?? null;
        autoTitleHistory.delete(sessionId);
        const session: ChatSession = {
            id: sessionId,
            title: title || `对话 ${new Date().toLocaleString()}`,
            messages: [],
            created_at: new Date(),
            updated_at: new Date(),
            workflow_id: null,
            session_id: sessionId,
            plan_id: null,
            plan_title: null,
            current_task_id: null,
            current_task_name: null,
            last_message_at: null,
            is_active: true,
            defaultSearchProvider: providerPreference,
            defaultBaseModel: baseModelPreference,
            defaultLLMProvider: llmProviderPreference,
            titleSource: 'local',
            isUserNamed: false,
        };

        console.log('🆕 创建新会话:', {
            前端会话ID: session.id,
            后端会话ID: session.session_id,
            标题: session.title
        });

        get().addSession(session);
        get().setCurrentSession(session);
        set({ currentWorkflowId: null });

        SessionStorage.setCurrentSessionId(sessionId);

        return session;
    },

    restoreSession: async (sessionId, title) => {
        let session = get().sessions.find((s) => s.id === sessionId) || null;

        if (!session) {
            await get().loadSessions();
            session = get().sessions.find((s) => s.id === sessionId) || null;
        }

        if (!session) {
            const providerPreference = get().defaultSearchProvider ?? null;
            const baseModelPreference = get().defaultBaseModel ?? null;
            const llmProviderPreference = get().defaultLLMProvider ?? null;
            autoTitleHistory.delete(sessionId);
            session = {
                id: sessionId,
                title: title || `对话 ${new Date().toLocaleString()}`,
                messages: [],
                created_at: new Date(),
                updated_at: new Date(),
                workflow_id: null,
                session_id: sessionId,
                plan_id: null,
                plan_title: null,
                current_task_id: null,
                current_task_name: null,
                last_message_at: null,
                is_active: true,
                defaultSearchProvider: providerPreference,
                defaultBaseModel: baseModelPreference,
                defaultLLMProvider: llmProviderPreference,
                titleSource: 'local',
                isUserNamed: false,
            };
            get().addSession(session);
        }

        get().setCurrentSession(session);
        SessionStorage.setCurrentSessionId(sessionId);

        await get().loadChatHistory(sessionId);

        const refreshedSession = get().currentSession;
        if (refreshedSession && refreshedSession.id === sessionId) {
            return refreshedSession;
        }

        return refreshedSession || session;
    },

    loadSessions: async () => {
        try {
            const response = await chatApi.getSessions({ limit: 100, offset: 0 });
            const summaries = response.sessions ?? [];
            const existingSessions = get().sessions;
            const existingMap = new Map(existingSessions.map((s) => [s.id, s]));

            const normalized = summaries.map((summary) => {
                const base = summaryToChatSession(summary);
                const existing = existingMap.get(summary.id);
                if (!existing) {
                    return base;
                }
                return {
                    ...base,
                    messages: existing.messages,
                    workflow_id: existing.workflow_id ?? base.workflow_id,
                    created_at: existing.created_at ?? base.created_at,
                    updated_at: base.updated_at,
                };
            });

            for (const session of normalized) {
                const sessionKey = session.session_id ?? session.id;
                if (!sessionKey) {
                    continue;
                }
                const source = session.titleSource ?? null;
                if (source && source !== 'default' && source !== 'local') {
                    autoTitleHistory.set(sessionKey, { planId: session.plan_id ?? null });
                }
            }

            set({ sessions: normalized });
            SessionStorage.setAllSessionIds(normalized.map((s) => s.id));

            const storedId = SessionStorage.getCurrentSessionId();
            const nextSession =
                (storedId && normalized.find((s) => s.id === storedId)) ||
                normalized[0] ||
                null;

            if (nextSession) {
                get().setCurrentSession(nextSession);
            } else {
                set({
                    currentSession: null,
                    messages: [],
                    currentPlanId: null,
                    currentPlanTitle: null,
                    currentTaskId: null,
                    currentTaskName: null,
                    currentWorkflowId: null,
                    defaultSearchProvider: null,
                    defaultBaseModel: null,
                });
                SessionStorage.clearCurrentSessionId();
            }
        } catch (error) {
            console.error('加载会话列表失败:', error);
            throw error;
        }
    },

    autotitleSession: async (sessionId, options = {}) => {
        const sessionKey = sessionId?.trim();
        if (!sessionKey) {
            return null;
        }

        if (pendingAutotitleSessions.has(sessionKey)) {
            return null;
        }

        pendingAutotitleSessions.add(sessionKey);

        const payload: { force?: boolean; strategy?: string | null } = {};
        if (options.force) {
            payload.force = true;
        }
        if (options.strategy !== undefined) {
            payload.strategy = options.strategy;
        }

        try {
            const result = await chatApi.autotitleSession(sessionKey, payload);
            set((state) => {
                const updateSession = (session: ChatSession): ChatSession => {
                    const matchId = session.session_id ?? session.id;
                    if (matchId !== sessionKey) {
                        return session;
                    }

                    const next: ChatSession = {
                        ...session,
                        title: result.title ?? session.title,
                        titleSource: result.source ?? session.titleSource ?? null,
                    };

                    if (result.skipped_reason === 'user_named') {
                        next.isUserNamed = true;
                    } else if (result.source === 'user') {
                        next.isUserNamed = true;
                    } else if (result.updated) {
                        next.isUserNamed = false;
                    }

                    return next;
                };

                const currentSession = state.currentSession
                    ? updateSession(state.currentSession)
                    : state.currentSession;

                return {
                    currentSession,
                    sessions: state.sessions.map(updateSession),
                };
            });

            const sessionsAfter = get().sessions;
            const target = sessionsAfter.find((session) => {
                const matchId = session.session_id ?? session.id;
                return matchId === sessionKey;
            });
            if (target) {
                autoTitleHistory.set(sessionKey, { planId: target.plan_id ?? null });
            }

            return result;
        } catch (error) {
            console.warn('自动生成会话标题失败:', error);
            throw error;
        } finally {
            pendingAutotitleSessions.delete(sessionKey);
        }
    },
});
