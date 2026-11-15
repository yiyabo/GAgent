import { ChatSliceCreator } from './types';
import { chatApi } from '@api/chat';
import { useTasksStore } from '@store/tasks';

export const createContextSlice: ChatSliceCreator = (set, get) => ({
  currentWorkflowId: null,
  currentPlanId: null,
  currentPlanTitle: null,
  currentTaskId: null,
  currentTaskName: null,
  planMode: false,

  setChatContext: ({ planId, planTitle, taskId, taskName }) => {
  set((state) => {
  const nextPlanId = planId !== undefined ? planId : state.currentPlanId;
  const nextPlanTitle = planTitle !== undefined ? planTitle : state.currentPlanTitle;
  const nextTaskId = taskId !== undefined ? taskId : state.currentTaskId;
  const nextTaskName = taskName !== undefined ? taskName : state.currentTaskName;

  if (
  state.currentPlanId === nextPlanId &&
  state.currentPlanTitle === nextPlanTitle &&
  state.currentTaskId === nextTaskId &&
  state.currentTaskName === nextTaskName
  ) {
  return state;
  }

  const planIdValue = nextPlanId ?? null;
  const planTitleValue = nextPlanTitle ?? null;

  const updatedSession = state.currentSession
  ? {
  ...state.currentSession,
  plan_id: planIdValue,
  plan_title: planTitleValue,
  }
  : null;

  const updatedSessions = updatedSession
  ? state.sessions.map((session) =>
  session.id === updatedSession.id ? updatedSession : session
  )
  : state.sessions;

  return {
  currentPlanId: planIdValue,
  currentPlanTitle: planTitleValue,
  currentTaskId: nextTaskId ?? null,
  currentTaskName: nextTaskName ?? null,
  currentSession: updatedSession,
  sessions: updatedSessions,
  };
  });
  },

  clearChatContext: () =>
  set((state) => {
  const updatedSession = state.currentSession
  ? { ...state.currentSession, plan_id: null, plan_title: null }
  : null;
  const sessions = updatedSession
  ? state.sessions.map((session) =>
  session.id === updatedSession.id ? updatedSession : session
  )
  : state.sessions;

  return {
  currentPlanId: null,
  currentPlanTitle: null,
  currentTaskId: null,
  currentTaskName: null,
  currentSession: updatedSession,
  sessions,
  };
  }),

  setPlanMode: async (enabled) => {
  const previous = {
  planMode: get().planMode,
  currentSession: get().currentSession,
  sessions: get().sessions,
  };
  const session = previous.currentSession;
  const settings = {
  ...(session?.settings ?? {}),
  plan_mode: enabled,
  ...(enabled ? { plan_offer_dismissed: false } : {}),
  };
  const currentSession = session ? { ...session, settings } : null;
  const sessions = currentSession
  ? previous.sessions.map((entry) => entry.id === currentSession.id ? currentSession : entry)
  : previous.sessions;
  set({ planMode: enabled, currentSession, sessions });

  if (!session) return;
  try {
  await chatApi.updateSession(session.session_id ?? session.id, { settings });
  } catch (error) {
  set(previous);
  throw error;
  }
  },

  dismissPlanModeOffer: async () => {
  const previous = {
  planMode: get().planMode,
  currentSession: get().currentSession,
  sessions: get().sessions,
  };
  const session = previous.currentSession;
  const settings = { ...(session?.settings ?? {}), plan_offer_dismissed: true };
  const currentSession = session ? { ...session, settings } : null;
  const sessions = currentSession
  ? previous.sessions.map((entry) => entry.id === currentSession.id ? currentSession : entry)
  : previous.sessions;
  set({ currentSession, sessions });

  if (!session) return;
  try {
  await chatApi.updateSession(session.session_id ?? session.id, { settings });
  } catch (error) {
  set(previous);
  throw error;
  }
  },

  setCurrentWorkflowId: (workflowId) => {
  const state = get();
  if (state.currentWorkflowId === workflowId) {
  return;
  }

  const currentSession = state.currentSession
  ? { ...state.currentSession, workflow_id: workflowId ?? undefined }
  : null;
  const sessions = state.sessions.map((session) =>
  session.id === currentSession?.id
  ? { ...session, workflow_id: workflowId ?? undefined }
  : session
  );

  try {
  const { setCurrentWorkflowId } = useTasksStore.getState();
  setCurrentWorkflowId(workflowId ?? null);
  } catch (err) {
  console.warn('Unable to sync workflow id to tasks store:', err);
  }

  set({
  currentWorkflowId: workflowId ?? null,
  currentSession,
  sessions,
  });
  },
});
