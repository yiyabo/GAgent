import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import zhCN from './locales/zh-CN.json';
import { applyLanguage, useLanguageStore } from './language';

export const resources = {
  en: { translation: en },
  'zh-CN': { translation: zhCN },
} as const;

/**
 * Initialised synchronously from inline resources, so the first paint is already in the right
 * language and there is no flash of keys. The store is the single owner of the choice; this module
 * only mirrors it into i18next and <html lang>.
 */
void i18n.use(initReactI18next).init({
  resources,
  lng: useLanguageStore.getState().language,
  fallbackLng: 'en',
  interpolation: { escapeValue: false }, // React already escapes
  returnNull: false,
});
applyLanguage(useLanguageStore.getState().language);

useLanguageStore.subscribe((state) => {
  if (i18n.language !== state.language) void i18n.changeLanguage(state.language);
  applyLanguage(state.language);
});

export default i18n;
