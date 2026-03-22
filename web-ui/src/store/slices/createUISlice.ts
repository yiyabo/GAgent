import { ChatSliceCreator } from './types';

export const createUISlice: ChatSliceCreator = (set) => ({
    inputText: '',
    isTyping: false,
    processingSessionIds: new Set<string>(),
    activeRunIds: new Map<string, string>(),
    isUpdatingProvider: false,
    isUpdatingBaseModel: false,
    isUpdatingLLMProvider: false,
    chatPanelVisible: true,
    chatPanelWidth: 400,

    setInputText: (text) => set({ inputText: text }),
    setIsTyping: (typing) => set({ isTyping: typing }),
    setSessionProcessing: (sessionId, processing) =>
        set((state) => {
            if (!sessionId) return state;
            const next = new Set(state.processingSessionIds);
            if (processing) next.add(sessionId);
            else next.delete(sessionId);
            return { processingSessionIds: next };
        }),
    setActiveRunId: (sessionKey, runId) =>
        set((state) => {
            const next = new Map(state.activeRunIds);
            if (runId) next.set(sessionKey, runId);
            else next.delete(sessionKey);
            return { activeRunIds: next };
        }),
    toggleChatPanel: () => set((state) => ({ chatPanelVisible: !state.chatPanelVisible })),
    setChatPanelVisible: (visible) => set({ chatPanelVisible: visible }),
    setChatPanelWidth: (width) => set({ chatPanelWidth: width }),
});
