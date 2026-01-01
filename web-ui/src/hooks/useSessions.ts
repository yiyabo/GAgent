import { useQuery, useMutation } from '@tanstack/react-query';
import { chatApi } from '@api/chat';
import { queryClient } from '@/queryClient';
import { useChatStore } from '@store/chat';
import { ChatSessionSummary, ChatSession, ChatSessionsResponse } from '@/types';
import { summaryToChatSession } from '@store/chatUtils';

export const SESSIONS_QUERY_KEY = ['sessions'];

export function useSessions() {
    return useQuery({
        queryKey: SESSIONS_QUERY_KEY,
        queryFn: () => chatApi.getSessions({ limit: 100 }),
        select: (data) => data.sessions.map(summaryToChatSession),
    });
}

export function useDeleteSession() {
    const removeSession = useChatStore((state) => state.removeSession);
    const currentSession = useChatStore((state) => state.currentSession);
    const setCurrentSession = useChatStore((state) => state.setCurrentSession);

    return useMutation({
        mutationFn: (variables: { sessionId: string; options?: { archive?: boolean } }) =>
            chatApi.deleteSession(variables.sessionId, variables.options),
        onMutate: async ({ sessionId, options }) => {
            // Cancel any outgoing refetches
            await queryClient.cancelQueries({ queryKey: SESSIONS_QUERY_KEY });

            // Snapshot the previous value
            const previousData = queryClient.getQueryData<any>(SESSIONS_QUERY_KEY);

            // Optimistically update to the new value
            if (previousData) {
                // Defensive check: handle both object { sessions: [] } and raw array [] formats
                const isObjectFormat = previousData && typeof previousData === 'object' && Array.isArray(previousData.sessions);
                const isArrayFormat = Array.isArray(previousData);

                if (isObjectFormat) {
                    queryClient.setQueryData<ChatSessionsResponse>(
                        SESSIONS_QUERY_KEY,
                        {
                            ...previousData,
                            sessions: previousData.sessions.filter((s: any) => s.id !== sessionId)
                        }
                    );
                } else if (isArrayFormat) {
                    queryClient.setQueryData<any>(
                        SESSIONS_QUERY_KEY,
                        previousData.filter((s: any) => s.id !== sessionId)
                    );
                }
            }

            // If archiving, we might want to just update the status, but here we usually filter
            // If the deleted session was the current one, handle fallback in Zustand
            if (currentSession?.id === sessionId) {
                // Fallback logic handled in the component usually, but we could add it here
            }

            return { previousSessions: previousData };
        },
        onError: (err, variables, context) => {
            if (context?.previousSessions) {
                queryClient.setQueryData(SESSIONS_QUERY_KEY, context.previousSessions);
            }
        },
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY });
        },
        onSuccess: (_, { sessionId }) => {
            // Also update local Zustand store to keep it in sync for state that isn't query-managed
            removeSession(sessionId);
        }
    });
}

export function useAutoTitleSession() {
    return useMutation({
        mutationFn: (variables: { sessionId: string; options?: { force?: boolean } }) =>
            chatApi.autotitleSession(variables.sessionId, variables.options),
        onSuccess: (result) => {
            // Invalidate sessions to get the new title
            void queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY });
        },
    });
} export function useUpdateSession() {
    return useMutation({
        mutationFn: (variables: { sessionId: string; payload: any }) =>
            chatApi.updateSession(variables.sessionId, variables.payload),
        onMutate: async ({ sessionId, payload }) => {
            await queryClient.cancelQueries({ queryKey: SESSIONS_QUERY_KEY });
            const previousData = queryClient.getQueryData<any>(SESSIONS_QUERY_KEY);

            if (previousData) {
                const isObjectFormat = previousData && typeof previousData === 'object' && Array.isArray(previousData.sessions);
                const isArrayFormat = Array.isArray(previousData);

                if (isObjectFormat) {
                    queryClient.setQueryData<ChatSessionsResponse>(
                        SESSIONS_QUERY_KEY,
                        {
                            ...previousData,
                            sessions: previousData.sessions.map((s: any) =>
                                s.id === sessionId ? { ...s, name: payload.title ?? payload.name ?? s.name } : s
                            )
                        }
                    );
                } else if (isArrayFormat) {
                    queryClient.setQueryData<any>(
                        SESSIONS_QUERY_KEY,
                        previousData.map((s: any) =>
                            s.id === sessionId ? { ...s, name: payload.title ?? payload.name ?? s.name } : s
                        )
                    );
                }
            }

            return { previousSessions: previousData };
        },
        onError: (err, variables, context) => {
            if (context?.previousSessions) {
                queryClient.setQueryData(SESSIONS_QUERY_KEY, context.previousSessions);
            }
        },
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY });
        },
    });
}
