import { create } from 'zustand';

/** The two UI languages. Add a file under locales/ and extend this union to add another. */
export type Language = 'zh-CN' | 'en';

export const LANGUAGES: readonly { code: Language; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'zh-CN', label: '中文' },
];

export const LANGUAGE_STORAGE_KEY = 'gagent.language';

export const isLanguage = (value: unknown): value is Language => value === 'zh-CN' || value === 'en';

/**
 * The language a visitor gets before they have chosen one. It is a property of the deployment, not
 * of the visitor's browser: CityU runs English, the .8 production host runs Chinese. Set it at build
 * time with VITE_DEFAULT_LANGUAGE=zh-CN | en. Unset or unknown falls back to English.
 */
export function deploymentDefault(raw: unknown = import.meta.env.VITE_DEFAULT_LANGUAGE): Language {
  return isLanguage(raw) ? raw : 'en';
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window !== 'undefined' ? window.localStorage : undefined;
  } catch {
    return undefined; // storage blocked (private mode, sandboxed iframe)
  }
}

/** A stored choice wins over the deployment default. Garbage in storage is ignored. */
export function readStoredLanguage(storage: Pick<Storage, 'getItem'> | undefined = safeStorage()): Language | null {
  try {
    const value = storage?.getItem(LANGUAGE_STORAGE_KEY);
    return isLanguage(value) ? value : null;
  } catch {
    return null;
  }
}

export function resolveLanguage(stored: Language | null, fallback: Language = deploymentDefault()): Language {
  return stored ?? fallback;
}

/** Writes <html lang> so screen readers, hyphenation and CJK font selection follow the UI language. */
export function applyLanguage(language: Language, root: HTMLElement | undefined = typeof document !== 'undefined' ? document.documentElement : undefined) {
  if (root) root.setAttribute('lang', language);
}

interface LanguageState {
  language: Language;
  setLanguage: (language: Language) => void;
}

export const useLanguageStore = create<LanguageState>((set) => ({
  language: resolveLanguage(readStoredLanguage()),
  setLanguage: (language) => {
    try {
      safeStorage()?.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch {
      /* the choice still applies for this session */
    }
    set({ language });
  },
}));
