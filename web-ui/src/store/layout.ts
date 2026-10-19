import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { readWorkspaceMode, saveWorkspaceMode, type WorkspaceMode } from './workspacePreference';

interface LayoutState {
  workspaceMode: WorkspaceMode;
  workspacePlanWidth: number | null;
  setWorkspaceMode: (mode: WorkspaceMode) => void;
  setWorkspacePlanWidth: (width: number) => void;
  chatListVisible: boolean;
  chatListWidth: number;
  dagSidebarWidth: number;
  dagSidebarFullscreen: boolean;
  toggleChatList: () => void;
  setChatListVisible: (visible: boolean) => void;
  setChatListWidth: (width: number) => void;
  setDagSidebarWidth: (width: number) => void;
  toggleDagSidebarFullscreen: () => void;
  setDagSidebarFullscreen: (fullscreen: boolean) => void;
}

export const useLayoutStore = create<LayoutState>()(
  subscribeWithSelector((set) => ({
    workspaceMode: readWorkspaceMode(),
    workspacePlanWidth: null,
    setWorkspaceMode: (mode) => {
      saveWorkspaceMode(mode);
      set({ workspaceMode: mode, dagSidebarFullscreen: false });
    },
    setWorkspacePlanWidth: (width) => set({ workspacePlanWidth: width }),
    chatListVisible: true,
    chatListWidth: 280,
    dagSidebarWidth: 400,
    dagSidebarFullscreen: false,
    toggleChatList: () =>
      set((state) => ({ chatListVisible: !state.chatListVisible })),
    setChatListVisible: (visible) => set({ chatListVisible: visible }),
    setChatListWidth: (width) => set({ chatListWidth: width }),
    setDagSidebarWidth: (width) => set({ dagSidebarWidth: width }),
    toggleDagSidebarFullscreen: () =>
      set((state) => ({ dagSidebarFullscreen: !state.dagSidebarFullscreen })),
    setDagSidebarFullscreen: (fullscreen) => set({ dagSidebarFullscreen: fullscreen }),
  }))
);
