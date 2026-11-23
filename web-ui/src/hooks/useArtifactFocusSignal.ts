import { useEffect, useRef } from 'react';
import { useLayoutStore } from '@store/layout';

/** Runs `onFocus` for each deliverable focus request made after this component mounted. */
export function useArtifactFocusSignal(onFocus: () => void): void {
  const nonce = useLayoutStore((state) => state.artifactFocusRequest?.nonce ?? null);
  const seen = useRef(nonce);
  const callback = useRef(onFocus);
  callback.current = onFocus;
  useEffect(() => {
    if (nonce == null || nonce === seen.current) return;
    seen.current = nonce;
    callback.current();
  }, [nonce]);
}
