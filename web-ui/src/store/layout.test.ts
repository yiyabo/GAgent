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

describe('plan overview height preference', () => {
  const HEIGHT_KEY = 'gagent.layout.planTopHeight';
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it('defaults to automatic and persists a dragged height', async () => {
    const first = await import('./layout');
    expect(first.useLayoutStore.getState().planTopHeight).toBeNull();
    first.useLayoutStore.getState().setPlanTopHeight(212.6);
    expect(first.useLayoutStore.getState().planTopHeight).toBe(213);
    expect(localStorage.getItem(HEIGHT_KEY)).toBe('213');

    vi.resetModules();
    const second = await import('./layout');
    expect(second.useLayoutStore.getState().planTopHeight).toBe(213);
  });

  it('reset clears the saved height and ignores corrupt values', async () => {
    localStorage.setItem(HEIGHT_KEY, 'not-a-number');
    vi.resetModules();
    const mod = await import('./layout');
    expect(mod.useLayoutStore.getState().planTopHeight).toBeNull();
    mod.useLayoutStore.getState().setPlanTopHeight(180);
    mod.useLayoutStore.getState().setPlanTopHeight(null);
    expect(mod.useLayoutStore.getState().planTopHeight).toBeNull();
    expect(localStorage.getItem(HEIGHT_KEY)).toBeNull();
  });
});
