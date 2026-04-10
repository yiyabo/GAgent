import { useMemo } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ENV } from '@/config/env';
import { ChatMessage } from '@/types';
import { buildToolResultsCache, resolveHistoryCursor } from '@store/chatUtils';
import { hydratePersistedMessage } from '@store/slices/message/historyHydration';
import { useChatStore } from '@store/chat';

export const MESSAGES_QUERY_KEY = (sessionId: string) => ['messages', sessionId];

export function useMessages(sessionId: string | null | undefined) {
    const liveMessages = useChatStore((state) => state.messages);
    const fallbackToolResults = useMemo(
        () => buildToolResultsCache(liveMessages),
        [liveMessages]
    );

    return useInfiniteQuery({
        queryKey: MESSAGES_QUERY_KEY(sessionId || ''),
        queryFn: async ({ pageParam }) => {
            if (!sessionId) return { messages: [], has_more: false, next_before_id: null };

            const query = new URLSearchParams({ limit: '50' });
            if (pageParam) {
                query.set('before_id', String(pageParam));
            }

            const response = await fetch(
                `${ENV.API_BASE_URL}/chat/history/${sessionId}?${query.toString()}`
            );

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();

            const messages: ChatMessage[] = (data.messages || []).map((msg: any, index: number) => {
                return hydratePersistedMessage({
                    sessionId,
                    rawMessage: msg,
                    index,
                    fallbackToolResults,
                });
            });

            return {
                messages,
                has_more: data.has_more,
                next_before_id: data.next_before_id ?? resolveHistoryCursor(messages),
            };
        },
        getNextPageParam: (lastPage) => lastPage.has_more ? lastPage.next_before_id : undefined,
        enabled: !!sessionId,
    });
}
