import i18n from 'i18next';

/** The BCP 47 tag for `toLocale*String()` and `Intl`, following the UI language rather than the browser's. */
/** The dayjs locale name for the UI language, for `dayjs(...).locale(...)` on a single value. */
export const dayjsLocale = (): 'zh-cn' | 'en' => (i18n.language === 'zh-CN' ? 'zh-cn' : 'en');

export const dateLocale = (): 'zh-CN' | 'en-US' => (i18n.language === 'zh-CN' ? 'zh-CN' : 'en-US');
