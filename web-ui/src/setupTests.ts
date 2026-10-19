import '@testing-library/jest-dom';
import '@/i18n';
import { useLanguageStore } from '@/i18n/language';

// Tests assert on visible text, so they must not depend on the deployment default (a build setting).
// Pin Chinese, the language the existing assertions were written in; i18n tests switch it explicitly.
useLanguageStore.setState({ language: 'zh-CN' });

if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
