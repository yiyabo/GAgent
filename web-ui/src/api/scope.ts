import { useChatStore } from '@store/chat';

export interface ScopeOverrides {
  session_id?: string | null;
  workflow_id?: string | null;
}

/**
 * Resolve API scope parameters (session/workflow) with optional overrides.
 * Ensures we always propagate the current chat context when calling backend APIs.
 */
export const resolveScopeParams = (overrides?: ScopeOverrides): Record<string, string> => {
  const state = useChatStore.getState();
  const session = state.currentSession;
  const workflowIdFromStore = state.currentWorkflowId ?? session?.workflow_id ?? null;

  const sessionCandidate = overrides?.session_id ?? session?.session_id ?? null;
  const workflowCandidate = overrides?.workflow_id ?? workflowIdFromStore ?? null;

  const params: Record<string, string> = {};
  if (sessionCandidate) {
    params.session_id = sessionCandidate;
  }
  if (workflowCandidate) {
    params.workflow_id = workflowCandidate;
  }
  return params;
};

/**
 * Merge arbitrary query params with resolved scope information.
 * Explicit params take precedence over resolved scope values.
 */
export const mergeWithScope = (
  params?: Record<string, any>,
  overrides?: ScopeOverrides
): Record<string, any> => {
  const scope = resolveScopeParams(overrides);
  return {
    ...(params ?? {}),
    ...scope,
  };
};
