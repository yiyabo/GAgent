import React, { useEffect, useMemo } from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { buildAntdTheme } from './antdTheme';
import { applyMode, useThemeStore, watchSystemScheme } from './themeMode';

/** Keeps <html data-theme> and the antd theme in step with the user's choice. */
const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const mode = useThemeStore((state) => state.mode);
  const config = useMemo(() => buildAntdTheme(mode), [mode]);

  useEffect(() => {
    applyMode(mode);
  }, [mode]);

  useEffect(() => watchSystemScheme(), []);

  return (
    <ConfigProvider locale={zhCN} theme={config}>
      {children}
    </ConfigProvider>
  );
};

export default ThemeProvider;
