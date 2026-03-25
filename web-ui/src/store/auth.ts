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
  setUser: (user: AuthUser | null) => void;
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
  setUser: (user) =>
    set({
      user,
      authenticated: Boolean(user),
      legacyAccessAllowed: false,
      initialized: true,
    }),
  clearAuth: () =>
    {
      resetClientStateForAuthChange();
      set({
        user: null,
        authenticated: false,
        legacyAccessAllowed: false,
        initialized: true,
        loading: false,
      });
    },
  bootstrap: async () => {
    set({ loading: true });
    try {
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
