import { describe, expect, it } from 'vitest';
import { isSessionArtifactFileUrl } from '../SessionArtifactImage';

describe('isSessionArtifactFileUrl', () => {
  it('returns true for session artifact file URLs', () => {
    expect(
      isSessionArtifactFileUrl('http://localhost:9000/artifacts/sessions/session_abc/file?path=results%2Fx.png'),
    ).toBe(true);
  });

  it('returns false for external images', () => {
    expect(isSessionArtifactFileUrl('https://example.com/a.png')).toBe(false);
  });

  it('returns false for non-http URLs', () => {
    expect(isSessionArtifactFileUrl('results/x.png')).toBe(false);
  });
});
