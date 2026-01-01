import { ChatSliceCreator } from './types';
import { useTasksStore } from '@store/tasks';
import { chatApi } from '@api/chat';
import { WebSearchProvider, BaseModelOption, LLMProviderOption } from '@/types';

export const createContextSlice: ChatSliceCreator = (set, get) => ({
    currentWorkflowId: null,
    currentPlanId: null,
    currentPlanTitle: null,
    currentTaskId: null,
    currentTaskName: null,
    defaultSearchProvider: null,
    defaultBaseModel: null,
    defaultLLMProvider: null,

    setChatContext: ({ planId, planTitle, taskId, taskName }) => {
        set((state) => {
            const nextPlanId = planId !== undefined ? planId : state.currentPlanId;
            const nextPlanTitle = planTitle !== undefined ? planTitle : state.currentPlanTitle;
            const nextTaskId = taskId !== undefined ? taskId : state.currentTaskId;
            const nextTaskName = taskName !== undefined ? taskName : state.currentTaskName;

            if (
                state.currentPlanId === nextPlanId &&
                state.currentPlanTitle === nextPlanTitle &&
                state.currentTaskId === nextTaskId &&
                state.currentTaskName === nextTaskName
            ) {
                return state;
            }

            const planIdValue = nextPlanId ?? null;
            const planTitleValue = nextPlanTitle ?? null;

            const updatedSession = state.currentSession
                ? {
                    ...state.currentSession,
                    plan_id: planIdValue,
                    plan_title: planTitleValue,
                }
                : null;

            const updatedSessions = updatedSession
                ? state.sessions.map((session) =>
                    session.id === updatedSession.id ? updatedSession : session
                )
                : state.sessions;

            return {
                currentPlanId: planIdValue,
                currentPlanTitle: planTitleValue,
                currentTaskId: nextTaskId ?? null,
                currentTaskName: nextTaskName ?? null,
                currentSession: updatedSession,
                sessions: updatedSessions,
            };
        });
    },

    clearChatContext: () =>
        set((state) => {
            const updatedSession = state.currentSession
                ? { ...state.currentSession, plan_id: null, plan_title: null }
                : null;
            const sessions = updatedSession
                ? state.sessions.map((session) =>
                    session.id === updatedSession.id ? updatedSession : session
                )
                : state.sessions;

            return {
                currentPlanId: null,
                currentPlanTitle: null,
                currentTaskId: null,
                currentTaskName: null,
                currentSession: updatedSession,
                sessions,
            };
        }),

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

    setDefaultSearchProvider: async (provider) => {
        const normalized: WebSearchProvider | null = provider ?? null;
        const prevProvider = get().defaultSearchProvider ?? null;
        if (normalized === prevProvider) {
            return;
        }

        const currentSession = get().currentSession;
        const sessionKey = currentSession?.session_id ?? currentSession?.id ?? null;

        set((state) => ({
            defaultSearchProvider: normalized,
            isUpdatingProvider: currentSession ? true : false,
            currentSession: currentSession
                ? { ...currentSession, defaultSearchProvider: normalized }
                : currentSession,
            sessions: currentSession
                ? state.sessions.map((session) =>
                    session.id === sessionKey
                        ? { ...session, defaultSearchProvider: normalized }
                        : session
                )
                : state.sessions,
        }));

        if (!currentSession) {
            set({ isUpdatingProvider: false });
            return;
        }

        try {
            if (!sessionKey) {
                set({ isUpdatingProvider: false });
                return;
            }

            await chatApi.updateSession(sessionKey, {
                settings: { default_search_provider: normalized },
            });
        } catch (error) {
            console.error('更新默认搜索提供商失败:', error);
            set((state) => ({
                defaultSearchProvider: prevProvider,
                isUpdatingProvider: false,
                currentSession: state.currentSession
                    ? { ...state.currentSession, defaultSearchProvider: prevProvider }
                    : state.currentSession,
                sessions: state.sessions.map((session) =>
                    session.id === sessionKey
                        ? { ...session, defaultSearchProvider: prevProvider }
                        : session
                ),
            }));
            throw error;
        }

        set((state) => ({
            isUpdatingProvider: false,
            defaultSearchProvider: normalized,
            currentSession: state.currentSession
                ? { ...state.currentSession, defaultSearchProvider: normalized }
                : state.currentSession,
            sessions: state.sessions.map((session) =>
                session.id === sessionKey
                    ? { ...session, defaultSearchProvider: normalized }
                    : session
            ),
        }));
    },

    setDefaultBaseModel: async (model) => {
        const normalized: BaseModelOption | null = model ?? null;
        const prevModel = get().defaultBaseModel ?? null;
        if (normalized === prevModel) {
            return;
        }

        const currentSession = get().currentSession;
        const sessionKey = currentSession?.session_id ?? currentSession?.id ?? null;

        set((state) => ({
            defaultBaseModel: normalized,
            isUpdatingBaseModel: currentSession ? true : false,
            currentSession: currentSession
                ? { ...currentSession, defaultBaseModel: normalized }
                : currentSession,
            sessions: currentSession
                ? state.sessions.map((session) =>
                    session.id === sessionKey
                        ? { ...session, defaultBaseModel: normalized }
                        : session
                )
                : state.sessions,
        }));

        if (!currentSession) {
            set({ isUpdatingBaseModel: false });
            return;
        }

        try {
            if (!sessionKey) {
                set({ isUpdatingBaseModel: false });
                return;
            }

            await chatApi.updateSession(sessionKey, {
                settings: { default_base_model: normalized },
            });
        } catch (error) {
            console.error('更新默认基座模型失败:', error);
            set((state) => ({
                defaultBaseModel: prevModel,
                isUpdatingBaseModel: false,
                currentSession: state.currentSession
                    ? { ...state.currentSession, defaultBaseModel: prevModel }
                    : state.currentSession,
                sessions: state.sessions.map((session) =>
                    session.id === sessionKey
                        ? { ...session, defaultBaseModel: prevModel }
                        : session
                ),
            }));
            throw error;
        }

        set((state) => ({
            isUpdatingBaseModel: false,
            defaultBaseModel: normalized,
            currentSession: state.currentSession
                ? { ...state.currentSession, defaultBaseModel: normalized }
                : state.currentSession,
            sessions: state.sessions.map((session) =>
                session.id === sessionKey
                    ? { ...session, defaultBaseModel: normalized }
                    : session
            ),
        }));
    },

    setDefaultLLMProvider: async (provider) => {
        const normalized: LLMProviderOption | null = provider ?? null;
        const prevProvider = get().defaultLLMProvider ?? null;
        if (normalized === prevProvider) {
            return;
        }

        const currentSession = get().currentSession;
        const sessionKey = currentSession?.session_id ?? currentSession?.id ?? null;

        set((state) => ({
            defaultLLMProvider: normalized,
            isUpdatingLLMProvider: currentSession ? true : false,
            currentSession: currentSession
                ? { ...currentSession, defaultLLMProvider: normalized }
                : currentSession,
            sessions: currentSession
                ? state.sessions.map((session) =>
                    session.id === sessionKey
                        ? { ...session, defaultLLMProvider: normalized }
                        : session
                )
                : state.sessions,
        }));

        if (!currentSession) {
            set({ isUpdatingLLMProvider: false });
            return;
        }

        try {
            if (!sessionKey) {
                set({ isUpdatingLLMProvider: false });
                return;
            }

            await chatApi.updateSession(sessionKey, {
                settings: { default_llm_provider: normalized },
            });
        } catch (error) {
            console.error('更新默认LLM提供商失败:', error);
            set((state) => ({
                defaultLLMProvider: prevProvider,
                isUpdatingLLMProvider: false,
                currentSession: state.currentSession
                    ? { ...state.currentSession, defaultLLMProvider: prevProvider }
                    : state.currentSession,
                sessions: state.sessions.map((session) =>
                    session.id === sessionKey
                        ? { ...session, defaultLLMProvider: prevProvider }
                        : session
                ),
            }));
            throw error;
        }

        set((state) => ({
            isUpdatingLLMProvider: false,
            defaultLLMProvider: normalized,
            currentSession: state.currentSession
                ? { ...state.currentSession, defaultLLMProvider: normalized }
                : state.currentSession,
            sessions: state.sessions.map((session) =>
                session.id === sessionKey
                    ? { ...session, defaultLLMProvider: normalized }
                    : session
            ),
        }));
    },
});
