import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { readWorkspaceMode, saveWorkspaceMode, type WorkspaceMode } from './workspacePreference';

const PLAN_PANEL_VISIBLE_KEY = 'gagent.layout.planPanelVisible';

function readPlanPanelVisible(): boolean {
  try {
    if (typeof window === 'undefined') return true;
    const raw = window.localStorage.getItem(PLAN_PANEL_VISIBLE_KEY);
    return raw === null ? true : raw === '1';
  } catch {
    return true;
  }
}

function savePlanPanelVisible(visible: boolean): void {
  try {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(PLAN_PANEL_VISIBLE_KEY, visible ? '1' : '0');
    }
  } catch {
    // Storage may be unavailable in private mode; visibility still works for this session.
  }
}

export interface ArtifactFocusRequest {
  sessionId: string;
  path: string;
  nonce: number;
}

interface LayoutState {
  workspaceMode: WorkspaceMode;
  workspacePlanWidth: number | null;
  setWorkspaceMode: (mode: WorkspaceMode) => void;
  setWorkspacePlanWidth: (width: number) => void;
  chatListVisible: boolean;
  chatListWidth: number;
  planPanelVisible: boolean;
  dagSidebarWidth: number;
  dagSidebarFullscreen: boolean;
  toggleChatList: () => void;
  setChatListVisible: (visible: boolean) => void;
  setChatListWidth: (width: number) => void;
  setPlanPanelVisible: (visible: boolean) => void;
  togglePlanPanel: () => void;
  setDagSidebarWidth: (width: number) => void;
  toggleDagSidebarFullscreen: () => void;
  setDagSidebarFullscreen: (fullscreen: boolean) => void;
  artifactFocusRequest: ArtifactFocusRequest | null;
  focusDeliverable: (sessionId: string, path: string) => void;
  clearArtifactFocus: (nonce: number) => void;
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
    planPanelVisible: readPlanPanelVisible(),
    dagSidebarWidth: 400,
    dagSidebarFullscreen: false,
    toggleChatList: () =>
      set((state) => ({ chatListVisible: !state.chatListVisible })),
    setChatListVisible: (visible) => set({ chatListVisible: visible }),
    setChatListWidth: (width) => set({ chatListWidth: width }),
    setPlanPanelVisible: (visible) => {
      savePlanPanelVisible(visible);
      set({ planPanelVisible: visible });
    },
    togglePlanPanel: () =>
      set((state) => {
        const next = !state.planPanelVisible;
        savePlanPanelVisible(next);
        return { planPanelVisible: next };
      }),
    setDagSidebarWidth: (width) => set({ dagSidebarWidth: width }),
    toggleDagSidebarFullscreen: () =>
      set((state) => ({ dagSidebarFullscreen: !state.dagSidebarFullscreen })),
    setDagSidebarFullscreen: (fullscreen) => set({ dagSidebarFullscreen: fullscreen }),
    artifactFocusRequest: null,
    focusDeliverable: (sessionId, path) => {
      savePlanPanelVisible(true);
      set((state) => ({
        planPanelVisible: true,
        artifactFocusRequest: { sessionId, path, nonce: (state.artifactFocusRequest?.nonce ?? 0) + 1 },
      }));
    },
    clearArtifactFocus: (nonce) =>
      set((state) => (state.artifactFocusRequest?.nonce === nonce ? { artifactFocusRequest: null } : {})),
  }))
);
