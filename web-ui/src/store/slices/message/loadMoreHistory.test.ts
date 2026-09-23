import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useChatStore } from '@store/chat';

/**
 * Single-source history pagination: loadMoreHistory funnels scroll-back
 * through the store's own loadChatHistory (the React Query side channel was
 * removed), guarded by hasMore/loading/cursor/session.
 */
describe('loadMoreHistory', () => {
    beforeEach(() => {
        useChatStore.setState({
            currentSession: { id: 's1', session_id: 's1' } as any,
            historyHasMore: true,
            historyLoading: false,
            historyBeforeId: 42,
        });
    });

    it('fetches the next older page with the stored cursor', async () => {
        const loadChatHistory = vi.fn().mockResolvedValue(undefined);
        useChatStore.setState({ loadChatHistory });

        await useChatStore.getState().loadMoreHistory();

        expect(loadChatHistory).toHaveBeenCalledTimes(1);
        expect(loadChatHistory).toHaveBeenCalledWith('s1', { beforeId: 42, append: true });
    });

    it('is a no-op without more pages', async () => {
        const loadChatHistory = vi.fn();
        useChatStore.setState({ loadChatHistory, historyHasMore: false });

        await useChatStore.getState().loadMoreHistory();

        expect(loadChatHistory).not.toHaveBeenCalled();
    });

    it('is a no-op while a history load is in flight', async () => {
        const loadChatHistory = vi.fn();
        useChatStore.setState({ loadChatHistory, historyLoading: true });

        await useChatStore.getState().loadMoreHistory();

        expect(loadChatHistory).not.toHaveBeenCalled();
    });

    it('is a no-op without a cursor or session', async () => {
        const loadChatHistory = vi.fn();
        useChatStore.setState({ loadChatHistory, historyBeforeId: null });
        await useChatStore.getState().loadMoreHistory();

        useChatStore.setState({ historyBeforeId: 42, currentSession: null });
        await useChatStore.getState().loadMoreHistory();

        expect(loadChatHistory).not.toHaveBeenCalled();
    });
});
