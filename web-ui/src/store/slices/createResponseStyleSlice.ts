import { chatApi } from '@api/chat';
import { useAuthStore } from '@store/auth';
import type { ChatSliceCreator } from './types';

export const createResponseStyleSlice: ChatSliceCreator = (set, get) => ({
  responseDefaults: null,
  responseDefaultsOwner: null,
  responseDefaultsRequest: null,
  responsePreferenceRevisions: {},
  responsePreferenceSaving: new Set<string>(),

  loadResponseDefaults: async () => {
    const user = useAuthStore.getState().user;
    if (!user || !useAuthStore.getState().authenticated) return;
    const owner = String(user.user_id);
    if (get().responseDefaultsOwner === owner) return;
    const request = `preferences_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    set({ responseDefaultsOwner: owner, responseDefaultsRequest: request, responseDefaults: null });
    try {
      const defaults = await chatApi.getResponsePreferences();
      if (useAuthStore.getState().user !== user || get().responseDefaultsRequest !== request) return;
      set((state) => ({
        responseDefaults: defaults,
        // Only still-empty local conversations inherit a late initial default response.
        sessions: state.sessions.map((session) => session.titleSource === 'local' && !session.messages.length && !session.settings
          ? { ...session, settings: { ...defaults } } : session),
        currentSession: state.currentSession?.titleSource === 'local' && !state.currentSession.messages.length && !state.currentSession.settings
          ? { ...state.currentSession, settings: { ...defaults } } : state.currentSession,
      }));
    } catch {
      // Chat remains usable with the server resolving defaults when a turn is submitted.
      if (get().responseDefaultsRequest === request && useAuthStore.getState().user === user)
        set({ responseDefaultsOwner: null, responseDefaultsRequest: null });
    }
  },

  updateResponsePreferences: async (sessionId, preferences, saveDefault = false) => {
    if (get().responsePreferenceSaving.has(sessionId)) return;
    const user = useAuthStore.getState().user;
    const session = get().sessions.find((entry) => entry.id === sessionId);
    if (!session) throw new Error('对话已不存在，请重新打开后设置');
    set((state) => ({
      responsePreferenceSaving: new Set([...state.responsePreferenceSaving, sessionId]),
      responsePreferenceRevisions: { ...state.responsePreferenceRevisions, [sessionId]: (state.responsePreferenceRevisions[sessionId] ?? 0) + 1 },
    }));
    try {
      const updated = await chatApi.updateSession(session.session_id ?? sessionId, {
        settings: { ...preferences }, save_response_defaults: saveDefault,
      });
      if (useAuthStore.getState().user !== user) return;
      set((state) => {
        const apply = (entry: typeof session) => entry.id === sessionId
          ? { ...entry, settings: { ...entry.settings, ...updated.settings } } : entry;
        return {
          sessions: state.sessions.map(apply),
          currentSession: state.currentSession ? apply(state.currentSession) : null,
          responsePreferenceRevisions: { ...state.responsePreferenceRevisions, [sessionId]: (state.responsePreferenceRevisions[sessionId] ?? 0) + 1 },
          ...(saveDefault ? {
            responseDefaults: { ...preferences }, responseDefaultsOwner: user ? String(user.user_id) : null,
            responseDefaultsRequest: null,
          } : {}),
        };
      });
    } finally {
      if (useAuthStore.getState().user === user) set((state) => {
        const saving = new Set(state.responsePreferenceSaving); saving.delete(sessionId);
        return { responsePreferenceSaving: saving };
      });
    }
  },
});
