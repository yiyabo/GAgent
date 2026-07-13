import { create } from 'zustand';
import { authApi } from '@api/auth';
import { projectApi } from '@api/project';
import { resetClientStateForAuthChange } from '@/auth/clientState';
import { useProjectStore } from './project';
import type { AuthUser } from '@/types/auth';

interface AuthState {
  user: AuthUser | null;
  authenticated: boolean;
  legacyAccessAllowed: boolean;
  initialized: boolean;
  loading: boolean;
  projectId: number | null;
  projectLabel: string | null;
  setUser: (user: AuthUser | null) => void;
  clearAuth: () => void;
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const trustedProjectState = (user: AuthUser | null) => {
  if (user?.access_mode !== 'platform') {
    return { projectId: null, projectLabel: null };
  }
  return {
    projectId: user.platform_context?.project_id ?? null,
    projectLabel: user.platform_context?.project_label ?? null,
  };
};

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  authenticated: false,
  legacyAccessAllowed: false,
  initialized: false,
  loading: false,
  projectId: null,
  projectLabel: null,
  setUser: (user) => set({
    user,
    authenticated: Boolean(user),
    legacyAccessAllowed: false,
    initialized: true,
    ...trustedProjectState(user),
  }),
  clearAuth: () => {
    resetClientStateForAuthChange();
    useProjectStore.getState().clearProject();
    set({
      user: null,
      authenticated: false,
      legacyAccessAllowed: false,
      initialized: true,
      loading: false,
      projectId: null,
      projectLabel: null,
    });
  },
  bootstrap: async () => {
    set({ loading: true });
    const urlParams = new URLSearchParams(window.location.search);
    let handoffToken = urlParams.get('__sso_handoff');
    let legacySessionToken = urlParams.get('__sso_session');
    if (!handoffToken && !legacySessionToken) {
      const nextRaw = urlParams.get('next');
      if (nextRaw) {
        try {
          const nextUrl = new URL(decodeURIComponent(nextRaw), window.location.origin);
          handoffToken = nextUrl.searchParams.get('__sso_handoff');
          legacySessionToken = nextUrl.searchParams.get('__sso_session');
        } catch {
          handoffToken = null;
          legacySessionToken = null;
        }
      }
    }
    const clearSsoParamsFromUrl = () => {
      urlParams.delete('__sso_handoff');
      urlParams.delete('__sso_session');
      urlParams.delete('project_id');
      urlParams.delete('user_id');
      urlParams.delete('project_label');
      const nextUrl = urlParams.toString()
        ? `${window.location.pathname}?${urlParams.toString()}`
        : window.location.pathname;
      window.history.replaceState({}, '', nextUrl);
    };
    const applyAuthenticatedUser = async (user: AuthUser) => {
      const projectState = trustedProjectState(user);
      set({
        user,
        authenticated: true,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
        ...projectState,
      });
      if (user.access_mode === 'platform' && projectState.projectId) {
        try {
          const response = await projectApi.getProject(projectState.projectId);
          if (response.code === 0 && response.data) {
            useProjectStore.getState().setProjectData(response.data);
          }
        } catch (error) {
          console.warn('[auth] platform project context unavailable', error);
        }
      } else {
        useProjectStore.getState().clearProject();
      }
    };

    try {
      if (handoffToken || legacySessionToken) {
        const mode = handoffToken ? 'handoff' : 'session';
        const token = handoffToken || legacySessionToken || '';
        try {
          const payload = await authApi.ssoComplete(token, mode);
          clearSsoParamsFromUrl();
          if (payload.authenticated && payload.user) {
            await applyAuthenticatedUser(payload.user);
            return;
          }
          console.error('[auth] SSO complete returned unauthenticated payload', payload);
        } catch (error) {
          console.error('[auth] SSO complete failed', error);
          clearSsoParamsFromUrl();
        }
      }

      const payload = await authApi.me();
      if (payload.authenticated && payload.user) {
        await applyAuthenticatedUser(payload.user);
        return;
      }
      resetClientStateForAuthChange();
      useProjectStore.getState().clearProject();
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: Boolean(payload.legacy_access_allowed),
        initialized: true,
        loading: false,
        projectId: null,
        projectLabel: null,
      });
    } catch {
      resetClientStateForAuthChange();
      useProjectStore.getState().clearProject();
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
        projectId: null,
        projectLabel: null,
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
        ...trustedProjectState(payload.user),
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
        ...trustedProjectState(payload.user),
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
      useProjectStore.getState().clearProject();
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
        projectId: null,
        projectLabel: null,
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
        ...trustedProjectState(payload.user),
      });
    } catch (error) {
      set({ loading: false });
      throw error;
    }
  },
}));
