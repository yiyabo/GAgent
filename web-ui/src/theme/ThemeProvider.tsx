import React, { useEffect, useMemo } from 'react';
import { ConfigProvider } from 'antd';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { buildAntdTheme } from './antdTheme';
import { applyMode, useThemeStore, watchSystemScheme } from './themeMode';
import { useLanguageStore } from '@/i18n/language';

const ANTD_LOCALES = { en: enUS, 'zh-CN': zhCN } as const;
const DAYJS_LOCALES = { en: 'en', 'zh-CN': 'zh-cn' } as const;

/** Keeps <html data-theme>, the antd theme and the antd/dayjs locale in step with the user's choices. */
const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const mode = useThemeStore((state) => state.mode);
  const language = useLanguageStore((state) => state.language);
  const config = useMemo(() => buildAntdTheme(mode), [mode]);

  useEffect(() => {
    applyMode(mode);
  }, [mode]);

  useEffect(() => watchSystemScheme(), []);

  // Set before children render the new language, so relative times ("3 minutes ago") match the UI.
  dayjs.locale(DAYJS_LOCALES[language]);

  return (
    <ConfigProvider locale={ANTD_LOCALES[language]} theme={config}>
      {children}
    </ConfigProvider>
  );
};

export default ThemeProvider;
