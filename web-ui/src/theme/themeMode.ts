import { create } from 'zustand';
import type { ThemeMode } from './antdTheme';

/** What the user picked. 'system' follows the OS and is the default. */
export type ThemePreference = ThemeMode | 'system';

export const THEME_STORAGE_KEY = 'gagent.theme';

const isPreference = (value: unknown): value is ThemePreference =>
  value === 'light' || value === 'dark' || value === 'system';

export function readStoredPreference(storage: Pick<Storage, 'getItem'> | undefined = safeStorage()): ThemePreference {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY);
    return isPreference(value) ? value : 'system';
  } catch {
    return 'system';
  }
}

export function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function resolveMode(preference: ThemePreference, prefersDark: boolean): ThemeMode {
  if (preference === 'system') return prefersDark ? 'dark' : 'light';
  return preference;
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window !== 'undefined' ? window.localStorage : undefined;
  } catch {
    return undefined; // storage blocked (private mode, sandboxed iframe)
  }
}

/** Writes data-theme on <html>; tokens.css switches every variable off that one attribute. */
export function applyMode(mode: ThemeMode, root: HTMLElement | undefined = typeof document !== 'undefined' ? document.documentElement : undefined) {
  if (!root) return;
  if (mode === 'dark') root.setAttribute('data-theme', 'dark');
  else root.removeAttribute('data-theme');
}

interface ThemeState {
  preference: ThemePreference;
  mode: ThemeMode;
  setPreference: (preference: ThemePreference) => void;
  /** Called when the OS colour scheme changes; only matters while the preference is 'system'. */
  syncSystem: (prefersDark: boolean) => void;
}

const initialPreference = readStoredPreference();
const initialMode = resolveMode(initialPreference, systemPrefersDark());

export const useThemeStore = create<ThemeState>((set, get) => ({
  preference: initialPreference,
  mode: initialMode,
  setPreference: (preference) => {
    try {
      safeStorage()?.setItem(THEME_STORAGE_KEY, preference);
    } catch {
      /* the choice still applies for this session */
    }
    const mode = resolveMode(preference, systemPrefersDark());
    applyMode(mode);
    set({ preference, mode });
  },
  syncSystem: (prefersDark) => {
    if (get().preference !== 'system') return;
    const mode = resolveMode('system', prefersDark);
    applyMode(mode);
    set({ mode });
  },
}));

/** Subscribe to OS changes. Returns the unsubscribe function. */
export function watchSystemScheme(): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  const onChange = (event: MediaQueryListEvent) => useThemeStore.getState().syncSystem(event.matches);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}
