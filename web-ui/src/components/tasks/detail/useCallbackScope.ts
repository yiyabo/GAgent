import { useCallback, useEffect, useRef } from 'react';

/** A completed request may outlive its UI owner; only that owner may apply callbacks. */
export function useCallbackScope(scope: string) {
  const state = useRef({ scope, generation: 0, mounted: false });
  if (state.current.scope !== scope) {
    state.current.scope = scope;
    state.current.generation += 1;
  }
  useEffect(() => {
    state.current.mounted = true;
    state.current.generation += 1;
    return () => {
      state.current.mounted = false;
      state.current.generation += 1;
    };
  }, []);
  return useCallback(() => {
    const generation = state.current.generation;
    return () => state.current.mounted && state.current.generation === generation;
  }, []);
}
