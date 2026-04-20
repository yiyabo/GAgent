import { describe, expect, it } from 'vitest';

import { shouldRenderWelcomeState } from './chatMainAreaState';

describe('shouldRenderWelcomeState', () => {
  it('shows the welcome state only when no visible messages exist and history is idle', () => {
    expect(shouldRenderWelcomeState(0, false)).toBe(true);
    expect(shouldRenderWelcomeState(1, false)).toBe(false);
    expect(shouldRenderWelcomeState(0, true)).toBe(false);
  });
});