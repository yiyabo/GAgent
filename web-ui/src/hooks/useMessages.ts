import { useInfiniteQuery } from '@tanstack/react-query';
import { ENV } from '@/config/env';
import { ChatMessage } from '@/types';
import { buildToolResultsCache, resolveHistoryCursor } from '@store/chatUtils';
import { collectToolResultsFromMetadata } from '@utils/toolResults';

export const MESSAGES_QUERY_KEY = (sessionId: string) => ['messages', sessionId];

export function useMessages(sessionId: string | null | undefined) {
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

            // Process messages similar to loadChatHistory in store
            const messages: ChatMessage[] = (data.messages || []).map((msg: any, index: number) => {
                const metadata =
                    msg.metadata && typeof msg.metadata === 'object'
                        ? { ...(msg.metadata as Record<string, any>) }
                        : {};
                if (typeof msg.id === 'number') {
                    metadata.backend_id = msg.id;
                }

                const toolResults = collectToolResultsFromMetadata(metadata.tool_results);
                if (toolResults.length > 0) {
                    metadata.tool_results = toolResults;
                }

                const backendId = typeof msg.id === 'number' ? msg.id : null;
                const messageId = backendId !== null ? `${sessionId}_${backendId}` : `${sessionId}_${index}`;

                return {
                    id: messageId,
                    type: (msg.role || 'assistant') as 'user' | 'assistant' | 'system',
                    content: msg.content,
                    timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(),
                    metadata,
                };
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
