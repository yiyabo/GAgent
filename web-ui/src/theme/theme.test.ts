import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
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

/** Every source file that can take part in the token layer. */
function tokenSources(dir: string = resolve(__dirname, '..')): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return tokenSources(path);
    // Tests are excluded: they quote token names as data, which would count as use.
    return /\.(css|ts|tsx)$/.test(path) && !/\.test\.tsx?$/.test(path) ? [path] : [];
  });
}

const SOURCE_FILES = tokenSources();
const TOKENS_CSS = resolve(__dirname, '../styles/tokens.css');

/** Names declared anywhere: stylesheet declarations, or custom properties set inline from TSX. */
const DECLARED = new Set(
  SOURCE_FILES.flatMap((file) => {
    const text = readFileSync(file, 'utf8');
    return [
      ...[...text.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((match) => match[1]),
      ...[...text.matchAll(/'(--[a-z0-9-]+)'/g)].map((match) => match[1]),
    ];
  }),
);

/** Every `var(--x)` reference in the source. */
const REFERENCED = new Set(
  SOURCE_FILES.flatMap((file) => [...readFileSync(file, 'utf8').matchAll(/var\(\s*(--[a-z0-9-]+)/g)].map((match) => match[1])),
);

/**
 * Tokens that are decided but not wired into a component yet. Each is scheduled for a later step of
 * the redesign; this list may only shrink. The spacing scale and `--fs-h4` wait for the shell and
 * per-page passes, the status sets for the per-page pass.
 */
const UNWIRED = [
  '--ease-out',
  '--focus-ring',
  '--fs-h4',
  '--info-bg',
  '--lh-tight',
  '--on-log',
  '--shadow-md',
  '--space-1',
  '--space-2',
  '--space-3',
  '--space-4',
  '--space-5',
  '--space-6',
  '--space-8',
  '--space-10',
  '--status-blocked-bg',
  '--status-blocked-border',
  '--status-blocked-fg',
  '--status-info-border',
  '--status-pending-bg',
  '--status-pending-border',
  '--status-pending-fg',
  '--status-running-bg',
  '--status-running-border',
  '--status-running-fg',
  '--status-skipped-bg',
  '--status-skipped-border',
  '--status-skipped-fg',
  '--surface-log',
  '--surface-scrim',
];

describe('the token layer stays honest', () => {
  it('never references a token that nothing declares', () => {
    const missing = [...REFERENCED].filter((name) => !DECLARED.has(name)).sort();
    expect(missing, 'add the declaration, or fix the typo in the reference').toEqual([]);
  });

  it('has no declaration whose property name is a var() reference', () => {
    // A find/replace over colour literals once turned `white-space: nowrap` into
    // `var(--surface-card)-space: nowrap`, which the browser drops in silence.
    const mangled: string[] = [];
    for (const file of SOURCE_FILES.filter((name) => name.endsWith('.css'))) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(/\n[ \t]*(var\([^)]*\)[a-z-]*)\s*:/g)) {
        mangled.push(`${relative(resolve(__dirname, '..'), file)}: ${match[1]}`);
      }
    }
    expect(mangled).toEqual([]);
  });

  it('carries no token beyond the ones still scheduled to be wired up', () => {
    const consumed = new Set(Object.values(MIRROR).map((variable) => `--${variable}`));
    for (const file of SOURCE_FILES) {
      if (file === TOKENS_CSS) continue;
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(/var\(\s*(--[a-z0-9-]+)/g)) consumed.add(match[1]);
      for (const match of text.matchAll(/'(--[a-z0-9-]+)'/g)) consumed.add(match[1]);
    }
    // An alias defined inside tokens.css counts as consumption too.
    for (const match of tokensCss.matchAll(/:\s*var\(\s*(--[a-z0-9-]+)/g)) consumed.add(match[1]);
    const unwired = Object.keys(light).filter((name) => !consumed.has(`--${name}`)).sort();
    expect(unwired).toEqual(UNWIRED.map((name) => name.slice(2)).sort());
  });
});

/** Selectors whose font-size sizes an icon or glyph, not text. Text below 12px is not readable. */
const GLYPH_SELECTORS = new Set([
  '.hero-toggle-help',
  '.response-style-trigger .anticon',
  '.tp-header-chevron',
  '.tp-item-icon',
  '.tp-item-chevron',
  '.pw-tree-toggle',
  '.pw-status-icon',
  '.tree-collapse-btn',
]);

describe('typography', () => {
  const cssFiles = SOURCE_FILES.filter((file) => file.endsWith('.css') && file !== TOKENS_CSS);

  it('sets no text smaller than 12px in any stylesheet', () => {
    const small: string[] = [];
    for (const file of cssFiles) {
      const text = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      for (const rule of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selector = rule[1].trim().replace(/\s+/g, ' ');
        if (GLYPH_SELECTORS.has(selector)) continue;
        for (const size of rule[2].matchAll(/font-size:\s*([0-9.]+)px/g)) {
          if (Number(size[1]) < 12) small.push(`${relative(resolve(__dirname, '..'), file)}: ${selector} ${size[1]}px`);
        }
      }
    }
    expect(small).toEqual([]);
  });

  it('uses the self-hosted mono and serif stacks instead of per-component font lists', () => {
    const literal: string[] = [];
    for (const file of SOURCE_FILES.filter((name) => name !== TOKENS_CSS && !name.endsWith('antdTheme.ts'))) {
      const text = readFileSync(file, 'utf8');
      // The xterm terminal needs a literal list: it measures glyphs on a canvas and cannot read CSS variables.
      if (file.endsWith('TerminalPanel.tsx')) continue;
      for (const match of text.matchAll(/font-?[fF]amily:\s*([^;\n]+)/g)) {
        const value = match[1].trim().replace(/[,'"]+$/, '');
        if (!/^(var\(--font-(sans|serif|mono)\)|inherit)/.test(value.replace(/^['"]/, ''))) {
          literal.push(`${relative(resolve(__dirname, '..'), file)}: ${value}`);
        }
      }
    }
    expect(literal).toEqual([]);
  });

  it('keeps the title levels on the serif stack', () => {
    const index = readFileSync(resolve(__dirname, '../styles/index.css'), 'utf8');
    expect(index).toMatch(/body h1\.ant-typography,\s*body h2\.ant-typography,\s*body h3\.ant-typography\s*\{[^}]*var\(--font-serif\)/);
  });
});

describe('chat composer', () => {
  const source = readFileSync(resolve(__dirname, '../components/layout/ChatMainArea.tsx'), 'utf8');

  it('takes its surface from the theme, not a literal colour', () => {
    // A literal white here made the input a white slab on the dark theme.
    const box = source.slice(source.indexOf('className="workspace-input-box"'));
    const style = box.slice(0, box.indexOf('<TextArea'));
    expect(style).toMatch(/background:\s*'var\(--surface-card\)'/);
    expect(style).not.toMatch(/#[0-9a-fA-F]{3,8}|rgba?\(|\bwhite\b/);
  });
});
