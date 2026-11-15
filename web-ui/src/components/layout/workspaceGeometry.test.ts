import { describe, expect, it } from 'vitest';
import { planPanelState, workspaceGeometry } from './workspaceGeometry';
import { readWorkspaceMode, saveWorkspaceMode, WORKSPACE_PREFERENCE_KEY } from '@store/workspacePreference';

describe('workspace layout recovery and resizing', () => {
  it('keeps a usable chat column after dragging a wide plan pane or shrinking the window', () => {
    for (const width of [1120, 1280, 1600]) {
      const layout = workspaceGeometry(width, true, 5000);
      expect(width - layout.navWidth - 8 - layout.planWidth).toBeGreaterThanOrEqual(340);
      expect(layout.compact).toBe(false);
    }
    expect(workspaceGeometry(390, true, 900).compact).toBe(true);
    expect(workspaceGeometry(390, true, 900).navWidth).toBe(0);
  });

  it('lets the recovery URL override preferences, then preserves an explicit layout choice', () => {
    localStorage.setItem(WORKSPACE_PREFERENCE_KEY, 'plan');
    window.history.replaceState({}, '', '/chat?layout=classic&project_id=25');
    expect(readWorkspaceMode()).toBe('classic');
    saveWorkspaceMode('plan');
    expect(readWorkspaceMode()).toBe('plan');
    expect(new URLSearchParams(window.location.search).get('project_id')).toBe('25');
    expect(localStorage.getItem(WORKSPACE_PREFERENCE_KEY)).toBe('plan');
    window.history.replaceState({}, '', '/chat');
    localStorage.removeItem(WORKSPACE_PREFERENCE_KEY);
  });
});


describe('plan panel visibility', () => {
  it('hides the plan pane and divider on desktop while offering a reopen control', () => {
    expect(planPanelState({ compact: false, compactPlan: false, fullscreen: false, visible: false }))
      .toEqual({ showPlan: false, showDivider: false, showReopen: true });
    expect(planPanelState({ compact: false, compactPlan: false, fullscreen: false, visible: true }))
      .toEqual({ showPlan: true, showDivider: true, showReopen: false });
  });

  it('keeps compact and focus modes usable regardless of the desktop preference', () => {
    expect(planPanelState({ compact: true, compactPlan: true, fullscreen: false, visible: false }).showPlan).toBe(true);
    expect(planPanelState({ compact: true, compactPlan: false, fullscreen: false, visible: true }).showPlan).toBe(false);
    expect(planPanelState({ compact: false, compactPlan: false, fullscreen: true, visible: false }))
      .toEqual({ showPlan: true, showDivider: false, showReopen: false });
  });
});
