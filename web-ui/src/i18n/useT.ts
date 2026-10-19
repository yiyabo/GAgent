import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from 'i18next';
import type { TranslationKey } from './keys';

export type TFn = (key: TranslationKey, values?: Record<string, string | number>) => string;

/**
 * The translate function every component uses. Same behaviour as react-i18next's `t`, but the key
 * is typed against the English catalogue, so a typo is a compile error instead of a raw key on
 * screen. Also re-renders the component when the language changes.
 */
export function useT(): TFn {
  const { t } = useTranslation();
  return useCallback<TFn>((key, values) => String(t(key as never, values as never)), [t]);
}

/** For code outside React (store actions, toasts fired from utilities). Reads the live language. */
export const translate: TFn = (key, values) => String(i18n.t(key as never, values as never));
