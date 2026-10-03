export type WorkspaceMode = 'plan' | 'classic';
export const WORKSPACE_PREFERENCE_KEY = 'gagent.workspace.layout.v1';

export function readWorkspaceMode(): WorkspaceMode {
  if (typeof window === 'undefined') return 'plan';
  const requested = new URLSearchParams(window.location.search).get('layout');
  if (requested === 'classic' || requested === 'plan') return requested;
  try {
    return window.localStorage.getItem(WORKSPACE_PREFERENCE_KEY) === 'classic' ? 'classic' : 'plan';
  } catch {
    return 'plan';
  }
}

export function saveWorkspaceMode(mode: WorkspaceMode): void {
  if (typeof window === 'undefined') return;
  try { window.localStorage.setItem(WORKSPACE_PREFERENCE_KEY, mode); } catch { /* Optional preference only. */ }
  // A recovery URL must not override a later explicit choice on refresh.
  const url = new URL(window.location.href);
  if (url.searchParams.has('layout')) {
    url.searchParams.set('layout', mode);
    window.history.replaceState(window.history.state, '', url);
  }
}
