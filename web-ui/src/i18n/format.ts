import i18n from 'i18next';

/** The BCP 47 tag for `toLocale*String()` and `Intl`, following the UI language rather than the browser's. */
export const dateLocale = (): 'zh-CN' | 'en-US' => (i18n.language === 'zh-CN' ? 'zh-CN' : 'en-US');
