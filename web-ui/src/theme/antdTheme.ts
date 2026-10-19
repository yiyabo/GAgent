import { theme as antdTheme, type ThemeConfig } from 'antd';

/**
 * antd theme for Phage-Agent. The values mirror src/styles/tokens.css one to one:
 * antd's colour algorithm needs literal colours, so they are repeated here, and
 * theme.test.ts fails if a value stops matching its CSS variable.
 */

export type ThemeMode = 'light' | 'dark';

export interface ThemePalette {
  brand: string;
  action: string;
  actionHover: string;
  actionBg: string;
  actionBorder: string;
  onAction: string;
  page: string;
  sidebar: string;
  card: string;
  raised: string;
  sunken: string;
  hover: string;
  selected: string;
  textPrimary: string;
  textSecondary: string;
  textTertiary: string;
  textQuaternary: string;
  textDisabled: string;
  borderSubtle: string;
  borderDefault: string;
  success: string;
  successBg: string;
  successBorder: string;
  warning: string;
  warningBg: string;
  warningBorder: string;
  error: string;
  errorBg: string;
  errorBorder: string;
}

export const PALETTES: Record<ThemeMode, ThemePalette> = {
  light: {
    brand: '#122c6f',
    action: '#1a64c8',
    actionHover: '#1555ad',
    actionBg: '#e8f1fd',
    actionBorder: '#a9c8f2',
    onAction: '#ffffff',
    page: '#fafbfd',
    sidebar: '#f2f5fa',
    card: '#ffffff',
    raised: '#ffffff',
    sunken: '#eff3f9',
    hover: '#e9eff8',
    selected: '#e1ebfa',
    textPrimary: '#16213a',
    textSecondary: '#46526c',
    textTertiary: '#56627d',
    textQuaternary: '#5f6c86',
    textDisabled: '#8a94aa',
    borderSubtle: '#e3e8f1',
    borderDefault: '#d6ddea',
    success: '#2b6e4a',
    successBg: '#e8f3ec',
    successBorder: '#a9d3b8',
    warning: '#8a5a00',
    warningBg: '#fbf3e0',
    warningBorder: '#e6cb8a',
    error: '#a3322b',
    errorBg: '#f9e9e7',
    errorBorder: '#e7b1ab',
  },
  dark: {
    brand: '#cfe0ff',
    action: '#5aa2ff',
    actionHover: '#7ab5ff',
    actionBg: '#12264a',
    actionBorder: '#2b4c85',
    onAction: '#0a1120',
    page: '#0d1526',
    sidebar: '#0a1120',
    card: '#121c32',
    raised: '#162139',
    sunken: '#162139',
    hover: '#16233f',
    selected: '#1b2c52',
    textPrimary: '#e6ecf8',
    textSecondary: '#8fa0c2',
    textTertiary: '#8294b8',
    textQuaternary: '#7d8db0',
    textDisabled: '#5d6c8c',
    borderSubtle: '#1d2a45',
    borderDefault: '#273656',
    success: '#8fd1b3',
    successBg: '#12302a',
    successBorder: '#2c5a4b',
    warning: '#e6c57a',
    warningBg: '#2e2512',
    warningBorder: '#5e4a1d',
    error: '#f0a39c',
    errorBg: '#33191a',
    errorBorder: '#6b2f2c',
  },
};

const FONT_SANS =
  "'Inter Variable','Noto Sans SC Variable','PingFang SC','Microsoft YaHei',system-ui,-apple-system,'Segoe UI',sans-serif";
const FONT_MONO = "'JetBrains Mono Variable','SF Mono',ui-monospace,Menlo,Consolas,monospace";

export function buildAntdTheme(mode: ThemeMode): ThemeConfig {
  const p = PALETTES[mode];
  return {
    algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: p.action,
      colorInfo: p.action,
      colorSuccess: p.success,
      colorWarning: p.warning,
      colorError: p.error,
      colorLink: p.action,
      colorLinkHover: p.actionHover,
      colorTextBase: p.textPrimary,
      colorText: p.textPrimary,
      colorTextSecondary: p.textSecondary,
      colorTextTertiary: p.textTertiary,
      colorTextQuaternary: p.textQuaternary,
      colorTextDisabled: p.textDisabled,
      colorBgBase: p.card,
      colorBgLayout: p.page,
      colorBgContainer: p.card,
      colorBgElevated: p.raised,
      colorBorder: p.borderDefault,
      colorBorderSecondary: p.borderSubtle,
      colorSplit: p.borderSubtle,
      colorFillContent: p.sunken,
      colorFillAlter: p.sunken,
      // antd derives hover/active/bg shades of the primary from a generated palette that comes out
      // muddy for this blue, so pin the ones the UI actually shows.
      colorPrimaryHover: p.actionHover,
      colorPrimaryBg: p.actionBg,
      colorPrimaryBorder: p.actionBorder,
      colorSuccessBg: p.successBg,
      colorSuccessBorder: p.successBorder,
      colorWarningBg: p.warningBg,
      colorWarningBorder: p.warningBorder,
      colorErrorBg: p.errorBg,
      colorErrorBorder: p.errorBorder,
      colorInfoBg: p.actionBg,
      colorInfoBorder: p.actionBorder,
      fontFamily: FONT_SANS,
      fontFamilyCode: FONT_MONO,
      fontSize: 14,
      fontSizeHeading1: 24,
      fontSizeHeading2: 20,
      fontSizeHeading3: 17,
      fontSizeHeading4: 15,
      lineHeight: 1.7,
      borderRadius: 6,
      borderRadiusSM: 4,
      borderRadiusLG: 8,
      borderRadiusXS: 4,
      controlHeight: 32,
      controlHeightSM: 28,
      controlHeightLG: 40,
      motionDurationFast: '0.15s',
      motionDurationMid: '0.22s',
      motionEaseOut: 'cubic-bezier(0.2, 0, 0, 1)',
      wireframe: false,
    },
    components: {
      Layout: { bodyBg: p.page, headerBg: p.card, siderBg: p.sidebar, headerHeight: 48, headerPadding: '0 20px' },
      Menu: {
        itemBg: 'transparent',
        itemSelectedBg: p.selected,
        itemSelectedColor: p.textPrimary,
        itemHoverBg: p.hover,
        itemHeight: 32,
      },
      Button: { primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none', fontWeight: 500, primaryColor: p.onAction },
      Table: { headerBg: p.sunken, headerColor: p.textSecondary, rowHoverBg: p.hover, headerSplitColor: 'transparent' },
      Tabs: { itemColor: p.textSecondary, itemSelectedColor: p.textPrimary, itemHoverColor: p.textPrimary, inkBarColor: p.action },
      Card: { boxShadowTertiary: 'none' },
      Tooltip: { colorBgSpotlight: mode === 'dark' ? '#2b3a5c' : '#16213a' },
      Progress: { remainingColor: p.borderSubtle },
      Skeleton: { gradientFromColor: p.sunken, gradientToColor: p.hover },
    },
  };
}
