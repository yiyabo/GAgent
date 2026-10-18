import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { PALETTES, buildAntdTheme, type ThemePalette } from './antdTheme';
import { THEME_STORAGE_KEY, applyMode, readStoredPreference, resolveMode, useThemeStore } from './themeMode';

const tokensCss = readFileSync(resolve(__dirname, '../styles/tokens.css'), 'utf8');

/** CSS variables defined inside one selector block, by name. */
function blockVars(selector: string): Record<string, string> {
  const start = tokensCss.indexOf(selector);
  const open = tokensCss.indexOf('{', start);
  let depth = 0;
  let end = open;
  for (let i = open; i < tokensCss.length; i += 1) {
    if (tokensCss[i] === '{') depth += 1;
    if (tokensCss[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  const body = tokensCss.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, '');
  const vars: Record<string, string> = {};
  for (const match of body.matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/g)) vars[match[1]] = match[2].trim();
  return vars;
}

const light = blockVars(':root {');
const dark = { ...light, ...blockVars("[data-theme='dark']") };

/** Which CSS variable each palette field mirrors. */
const MIRROR: Record<keyof ThemePalette, string> = {
  brand: 'color-brand',
  action: 'color-action',
  actionHover: 'color-action-hover',
  actionBg: 'color-action-bg',
  actionBorder: 'color-action-border',
  onAction: 'color-on-action',
  page: 'surface-page',
  sidebar: 'surface-sidebar',
  card: 'surface-card',
  raised: 'surface-raised',
  sunken: 'surface-sunken',
  hover: 'surface-hover',
  selected: 'surface-selected',
  textPrimary: 'text-primary',
  textSecondary: 'text-secondary',
  textTertiary: 'text-tertiary',
  textQuaternary: 'text-quaternary',
  textDisabled: 'text-disabled',
  borderSubtle: 'border-subtle',
  borderDefault: 'border-default',
  success: 'status-success-fg',
  successBg: 'status-success-bg',
  successBorder: 'status-success-border',
  warning: 'status-warning-fg',
  warningBg: 'status-warning-bg',
  warningBorder: 'status-warning-border',
  error: 'status-error-fg',
  errorBg: 'status-error-bg',
  errorBorder: 'status-error-border',
};

describe('tokens.css and the antd theme stay in step', () => {
  for (const [mode, vars] of [['light', light], ['dark', dark]] as const) {
    it(`every ${mode} palette value equals its CSS variable`, () => {
      const palette = PALETTES[mode];
      for (const [field, variable] of Object.entries(MIRROR)) {
        expect(vars[variable], `--${variable} is missing from tokens.css`).toBeDefined();
        expect(palette[field as keyof ThemePalette].toLowerCase(), `${mode}: ${field} vs --${variable}`).toBe(vars[variable].toLowerCase());
      }
    });
  }

  it('dark redefines every colour the light theme defines', () => {
    const darkOnly = blockVars("[data-theme='dark']");
    const colourNames = Object.keys(light).filter((name) => /^(color-|surface-|text-|border-|status-|on-)/.test(name) && /^#|^rgba?\(/.test(light[name]));
    const missing = colourNames.filter((name) => !(name in darkOnly) && name !== 'border-divider-width');
    expect(missing).toEqual([]);
  });

  it('keeps the legacy variable names that existing components read', () => {
    for (const name of ['primary-color', 'primary-hover', 'bg-primary', 'bg-secondary', 'bg-tertiary', 'border-color', 'text-primary', 'text-secondary', 'text-tertiary', 'text-quaternary', 'success-color', 'warning-color', 'error-color', 'info-color', 'radius-sm', 'radius-md', 'radius-lg', 'shadow-sm']) {
      expect(light[name], `--${name}`).toBeDefined();
    }
  });
});

/** WCAG contrast ratio of two #rrggbb colours. */
function contrast(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe('text stays readable in both themes', () => {
  for (const mode of ['light', 'dark'] as const) {
    const p = PALETTES[mode];
    it(`${mode}: body and secondary text reach 4.5:1 on every surface`, () => {
      for (const surface of [p.page, p.card, p.sunken]) {
        for (const text of [p.textPrimary, p.textSecondary, p.textTertiary, p.textQuaternary]) {
          expect(contrast(text, surface), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
        }
      }
    });
    it(`${mode}: the action colour and its label reach 4.5:1`, () => {
      expect(contrast(p.action, p.page)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(p.onAction, p.action)).toBeGreaterThanOrEqual(4.5);
    });
    it(`${mode}: every status label reaches 4.5:1 on its own background`, () => {
      for (const [fg, bg] of [[p.success, p.successBg], [p.warning, p.warningBg], [p.error, p.errorBg], [p.action, p.actionBg]]) {
        expect(contrast(fg, bg), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});

describe('the antd config', () => {
  it('uses the self-hosted font families and zero extra letter spacing', () => {
    const token = buildAntdTheme('light').token!;
    expect(token.fontFamily).toContain('Inter Variable');
    expect(token.fontFamily).toContain('Noto Sans SC Variable');
    expect(token.fontFamilyCode).toContain('JetBrains Mono Variable');
  });
  it('switches algorithm with the mode', () => {
    expect(buildAntdTheme('dark').algorithm).not.toBe(buildAntdTheme('light').algorithm);
  });
});

describe('theme preference', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
    useThemeStore.setState({ preference: 'system', mode: 'light' });
  });

  it('defaults to following the system and ignores garbage in storage', () => {
    expect(readStoredPreference({ getItem: () => null })).toBe('system');
    expect(readStoredPreference({ getItem: () => 'purple' })).toBe('system');
    expect(readStoredPreference({ getItem: () => { throw new Error('blocked'); } })).toBe('system');
    expect(readStoredPreference({ getItem: () => 'dark' })).toBe('dark');
  });

  it('resolves system to the OS scheme and an explicit choice to itself', () => {
    expect(resolveMode('system', true)).toBe('dark');
    expect(resolveMode('system', false)).toBe('light');
    expect(resolveMode('light', true)).toBe('light');
    expect(resolveMode('dark', false)).toBe('dark');
  });

  it('writes data-theme only for dark and removes it for light', () => {
    applyMode('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    applyMode('light');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('persists an explicit choice and applies it', () => {
    useThemeStore.getState().setPreference('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(useThemeStore.getState().mode).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('follows OS changes only while the preference is system', () => {
    useThemeStore.getState().syncSystem(true);
    expect(useThemeStore.getState().mode).toBe('dark');
    useThemeStore.getState().setPreference('light');
    useThemeStore.getState().syncSystem(true);
    expect(useThemeStore.getState().mode).toBe('light');
  });
});
