import { create } from 'zustand';
import { authApi } from '@api/auth';
import { resetClientStateForAuthChange } from '@/auth/clientState';
import type { AuthUser } from '@/types/auth';

interface AuthState {
  user: AuthUser | null;
  authenticated: boolean;
  legacyAccessAllowed: boolean;
  initialized: boolean;
  loading: boolean;
  projectId: number | null;
  setUser: (user: AuthUser | null) => void;
  setProjectId: (projectId: number | null) => void;
  clearAuth: () => void;
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  authenticated: false,
  legacyAccessAllowed: false,
  initialized: false,
  loading: false,
  projectId: null,
  setUser: (user) =>
    set({
      user,
      authenticated: Boolean(user),
      legacyAccessAllowed: false,
      initialized: true,
    }),
  setProjectId: (projectId) =>
    set({
      projectId,
    }),
  clearAuth: () =>
    {
      resetClientStateForAuthChange();
      localStorage.removeItem('project_id');
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
        projectId: null,
      });
    },
  bootstrap: async () => {
    set({ loading: true });
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const ssoSession = urlParams.get('__sso_session');
      const projectIdParam = urlParams.get('project_id');
      const projectId = projectIdParam ? parseInt(projectIdParam, 10) : null;
      
      if (projectId && !isNaN(projectId)) {
        set({ projectId });
        (window as any).__PROJECT_ID__ = projectId;
        // 持久化 project_id 到 localStorage
        localStorage.setItem('project_id', String(projectId));
      } else {
        // 尝试从 localStorage 恢复 project_id
        const storedProjectId = localStorage.getItem('project_id');
        if (storedProjectId) {
          const parsed = parseInt(storedProjectId, 10);
          if (!isNaN(parsed)) {
            set({ projectId: parsed });
            (window as any).__PROJECT_ID__ = parsed;
          }
        }
      }
      
      if (ssoSession) {
        try {
          const payload = await authApi.ssoComplete(ssoSession);
          urlParams.delete('__sso_session');
          urlParams.delete('project_id');
          const newUrl = urlParams.toString() 
            ? `${window.location.pathname}?${urlParams.toString()}`
            : window.location.pathname;
          window.history.replaceState({}, '', newUrl);
          if (payload.authenticated && payload.user) {
            set({
              user: payload.user,
              authenticated: true,
              legacyAccessAllowed: false,
              initialized: true,
              loading: false,
            });
            return;
          }
        } catch {
          urlParams.delete('__sso_session');
          urlParams.delete('project_id');
          const newUrl = urlParams.toString() 
            ? `${window.location.pathname}?${urlParams.toString()}`
            : window.location.pathname;
          window.history.replaceState({}, '', newUrl);
        }
      }

      const payload = await authApi.me();
      if (payload.authenticated && payload.user) {
        set({
          user: payload.user,
          authenticated: true,
          legacyAccessAllowed: false,
          initialized: true,
          loading: false,
        });
      } else {
        resetClientStateForAuthChange();
        set({
          user: null,
          authenticated: false,
          legacyAccessAllowed: Boolean(payload.legacy_access_allowed),
          initialized: true,
          loading: false,
        });
      }
    } catch {
      resetClientStateForAuthChange();
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
      });
    }
  },
  login: async (email, password) => {
    set({ loading: true });
    try {
      const payload = await authApi.login({ email, password });
      set({
        user: payload.user,
        authenticated: true,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
      });
    } catch (error) {
      set({ loading: false });
      throw error;
    }
  },
  register: async (email, password) => {
    set({ loading: true });
    try {
      const payload = await authApi.register({ email, password });
      set({
        user: payload.user,
        authenticated: true,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
      });
    } catch (error) {
      set({ loading: false });
      throw error;
    }
  },
  logout: async () => {
    try {
      await authApi.logout();
    } finally {
      resetClientStateForAuthChange();
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
      });
    }
  },
  changePassword: async (currentPassword, newPassword) => {
    set({ loading: true });
    try {
      const payload = await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      set({
        user: payload.user,
        authenticated: true,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
      });
    } catch (error) {
      set({ loading: false });
      throw error;
    }
  },
}));
