import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { readWorkspaceMode, saveWorkspaceMode, type WorkspaceMode } from './workspacePreference';

const PLAN_PANEL_VISIBLE_KEY = 'gagent.layout.planPanelVisible';
const PLAN_TOP_HEIGHT_KEY = 'gagent.layout.planTopHeight';

/** Height of the plan overview (title, meta, assumptions) above the task tabs; null = automatic. */
function readPlanTopHeight(): number | null {
  try {
    if (typeof window === 'undefined') return null;
    const raw = window.localStorage.getItem(PLAN_TOP_HEIGHT_KEY);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
  } catch {
    return null;
  }
}

function savePlanTopHeight(height: number | null): void {
  try {
    if (typeof window === 'undefined') return;
    if (height === null) window.localStorage.removeItem(PLAN_TOP_HEIGHT_KEY);
    else window.localStorage.setItem(PLAN_TOP_HEIGHT_KEY, String(Math.round(height)));
  } catch {
    // Storage may be unavailable in private mode; the height still applies for this session.
  }
}

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

interface LayoutState {
  workspaceMode: WorkspaceMode;
  workspacePlanWidth: number | null;
  setWorkspaceMode: (mode: WorkspaceMode) => void;
  setWorkspacePlanWidth: (width: number) => void;
  chatListVisible: boolean;
  chatListWidth: number;
  planPanelVisible: boolean;
  planTopHeight: number | null;
  setPlanTopHeight: (height: number | null) => void;
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
    planTopHeight: readPlanTopHeight(),
    setPlanTopHeight: (height) => {
      const next = height === null ? null : Math.round(height);
      savePlanTopHeight(next);
      set({ planTopHeight: next });
    },
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
  }))
);
