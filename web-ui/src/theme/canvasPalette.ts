import { useMemo } from 'react';
import { useThemeStore } from './themeMode';

/**
 * Colours for things that cannot read CSS variables: <canvas>, vis-network and three.js all take a
 * literal colour string. This reads the *resolved* value of each design token from the live document,
 * so the graphs follow the theme (light, dark, and any future palette) instead of carrying a second,
 * hand-copied set of hex codes that drifts from tokens.css.
 *
 * Call it again when the theme changes; `useCanvasPalette` does that for components.
 */
export interface CanvasPalette {
  page: string;
  card: string;
  sunken: string;
  textPrimary: string;
  textSecondary: string;
  textTertiary: string;
  borderSubtle: string;
  borderDefault: string;
  borderStrong: string;
  action: string;
  actionBg: string;
  actionBorder: string;
  onAction: string;
  success: string;
  successBg: string;
  warning: string;
  warningBg: string;
  error: string;
  errorBg: string;
  blocked: string;
  blockedBg: string;
  skipped: string;
  /** Translucent ink used for card shadows: black in light, deeper black in dark. */
  shadow: string;
  fontSans: string;
  fontSerif: string;
  fontMono: string;
}

/** Used when there is no DOM to read (unit tests, SSR). Mirrors the light tokens. */
export const FALLBACK_PALETTE: CanvasPalette = {
  page: '#fafbfd',
  card: '#ffffff',
  sunken: '#eff3f9',
  textPrimary: '#16213a',
  textSecondary: '#46526c',
  textTertiary: '#56627d',
  borderSubtle: '#e3e8f1',
  borderDefault: '#d6ddea',
  borderStrong: '#c3ccdd',
  action: '#1a64c8',
  actionBg: '#e8f1fd',
  actionBorder: '#a9c8f2',
  onAction: '#ffffff',
  success: '#2b6e4a',
  successBg: '#e8f3ec',
  warning: '#8a5a00',
  warningBg: '#fbf3e0',
  error: '#a3322b',
  errorBg: '#f9e9e7',
  blocked: '#7a3e8c',
  blockedBg: '#f3e9f6',
  skipped: '#5c636b',
  shadow: 'rgba(22, 33, 58, 0.12)',
  fontSans: 'Inter Variable, sans-serif',
  fontSerif: 'Source Serif 4 Variable, serif',
  fontMono: 'JetBrains Mono Variable, monospace',
};

/** token name -> palette field */
const TOKENS: Record<Exclude<keyof CanvasPalette, 'shadow'>, string> = {
  page: '--surface-page',
  card: '--surface-card',
  sunken: '--surface-sunken',
  textPrimary: '--text-primary',
  textSecondary: '--text-secondary',
  textTertiary: '--text-tertiary',
  borderSubtle: '--border-subtle',
  borderDefault: '--border-default',
  borderStrong: '--border-strong',
  action: '--color-action',
  actionBg: '--color-action-bg',
  actionBorder: '--color-action-border',
  onAction: '--color-on-action',
  success: '--status-success-fg',
  successBg: '--status-success-bg',
  warning: '--status-warning-fg',
  warningBg: '--status-warning-bg',
  error: '--status-error-fg',
  errorBg: '--status-error-bg',
  blocked: '--status-blocked-fg',
  blockedBg: '--status-blocked-bg',
  skipped: '--status-skipped-fg',
  fontSans: '--font-sans',
  fontSerif: '--font-serif',
  fontMono: '--font-mono',
};

/** The token names read by `readCanvasPalette`; theme.test.ts checks each one exists in tokens.css. */
export const CANVAS_TOKEN_NAMES: string[] = Object.values(TOKENS);

export function readCanvasPalette(root: Element | undefined = typeof document !== 'undefined' ? document.documentElement : undefined): CanvasPalette {
  if (!root || typeof getComputedStyle !== 'function') return FALLBACK_PALETTE;
  const style = getComputedStyle(root);
  const read = (token: string, fallback: string) => style.getPropertyValue(token).trim() || fallback;
  const palette = { ...FALLBACK_PALETTE };
  for (const [field, token] of Object.entries(TOKENS) as [Exclude<keyof CanvasPalette, 'shadow'>, string][]) {
    palette[field] = read(token, FALLBACK_PALETTE[field]);
  }
  // Shadows are ink, not a surface: darker on a dark page so the card still lifts off it.
  palette.shadow = root.getAttribute('data-theme') === 'dark' ? 'rgba(0, 0, 0, 0.55)' : 'rgba(22, 33, 58, 0.14)';
  return palette;
}

/** The palette for the current theme; recomputed when the user (or the OS) switches it. */
export function useCanvasPalette(): CanvasPalette {
  const mode = useThemeStore((state) => state.mode);
  // `mode` is the dependency on purpose: data-theme is written in the same store action, so by the
  // time this memo reruns the attribute is already set.
  return useMemo(() => readCanvasPalette(), [mode]);
}

/** A colour with alpha, for fills that sit on top of the page. Accepts #rgb, #rrggbb or rgb()/rgba(). */
export function withAlpha(colour: string, alpha: number): string {
  const a = Math.max(0, Math.min(1, alpha));
  const hex = colour.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split('').map((c) => c + c).join('') : hex[1];
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  const rgb = colour.trim().match(/^rgba?\(([^)]+)\)/i);
  if (rgb) {
    const [r, g, b] = rgb[1].split(',').map((v) => parseFloat(v));
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }
  return colour;
}
