import { ChatSliceCreator } from './types';

export const createUISlice: ChatSliceCreator = (set) => ({
    inputText: '',
    isTyping: false,
    isProcessing: false,
    isUpdatingProvider: false,
    isUpdatingBaseModel: false,
    isUpdatingLLMProvider: false,
    chatPanelVisible: true,
    chatPanelWidth: 400,

    setInputText: (text) => set({ inputText: text }),
    setIsTyping: (typing) => set({ isTyping: typing }),
    setIsProcessing: (processing) => set({ isProcessing: processing }),
    toggleChatPanel: () => set((state) => ({ chatPanelVisible: !state.chatPanelVisible })),
    setChatPanelVisible: (visible) => set({ chatPanelVisible: visible }),
    setChatPanelWidth: (width) => set({ chatPanelWidth: width }),
});
