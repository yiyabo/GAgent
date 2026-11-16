import { beforeEach, describe, expect, it, vi } from 'vitest';

const KEY = 'gagent.layout.planPanelVisible';

describe('plan panel preference', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it('defaults to visible and persists an explicit hide', async () => {
    const first = await import('./layout');
    expect(first.useLayoutStore.getState().planPanelVisible).toBe(true);

    first.useLayoutStore.getState().togglePlanPanel();
    expect(first.useLayoutStore.getState().planPanelVisible).toBe(false);
    expect(localStorage.getItem(KEY)).toBe('0');

    vi.resetModules();
    const second = await import('./layout');
    expect(second.useLayoutStore.getState().planPanelVisible).toBe(false);
  });

  it('restores a saved hidden preference', async () => {
    localStorage.setItem(KEY, '0');
    vi.resetModules();
    const mod = await import('./layout');
    expect(mod.useLayoutStore.getState().planPanelVisible).toBe(false);
  });
});
