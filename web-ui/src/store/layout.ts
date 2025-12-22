import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';

interface LayoutState {
  appSiderVisible: boolean;
  chatListVisible: boolean;
  chatListWidth: number;
  dagSidebarWidth: number;
  dagSidebarFullscreen: boolean;
  toggleAppSider: () => void;
  toggleChatList: () => void;
  setAppSiderVisible: (visible: boolean) => void;
  setChatListVisible: (visible: boolean) => void;
  setChatListWidth: (width: number) => void;
  setDagSidebarWidth: (width: number) => void;
  toggleDagSidebarFullscreen: () => void;
  setDagSidebarFullscreen: (fullscreen: boolean) => void;
}

export const useLayoutStore = create<LayoutState>()(
  subscribeWithSelector((set) => ({
    appSiderVisible: true,
    chatListVisible: true,
    chatListWidth: 280,
    dagSidebarWidth: 400,
    dagSidebarFullscreen: false,
    toggleAppSider: () =>
      set((state) => ({ appSiderVisible: !state.appSiderVisible })),
    toggleChatList: () =>
      set((state) => ({ chatListVisible: !state.chatListVisible })),
    setAppSiderVisible: (visible) => set({ appSiderVisible: visible }),
    setChatListVisible: (visible) => set({ chatListVisible: visible }),
    setChatListWidth: (width) => set({ chatListWidth: width }),
    setDagSidebarWidth: (width) => set({ dagSidebarWidth: width }),
    toggleDagSidebarFullscreen: () =>
      set((state) => ({ dagSidebarFullscreen: !state.dagSidebarFullscreen })),
    setDagSidebarFullscreen: (fullscreen) => set({ dagSidebarFullscreen: fullscreen }),
  }))
);
