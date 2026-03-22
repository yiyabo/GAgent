import { ChatSliceCreator } from './types';

export const createUISlice: ChatSliceCreator = (set) => ({
    inputText: '',
    isTyping: false,
    processingSessionIds: new Set<string>(),
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
    toggleChatPanel: () => set((state) => ({ chatPanelVisible: !state.chatPanelVisible })),
    setChatPanelVisible: (visible) => set({ chatPanelVisible: visible }),
    setChatPanelWidth: (width) => set({ chatPanelWidth: width }),
});
