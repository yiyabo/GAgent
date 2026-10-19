import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { CANVAS_TOKEN_NAMES, FALLBACK_PALETTE, readCanvasPalette, withAlpha } from './canvasPalette';

const tokensCss = readFileSync(resolve(__dirname, '../styles/tokens.css'), 'utf8');

/** Install a theme's real token values on <html>, the way the browser would resolve them. */
function installTokens(mode: 'light' | 'dark') {
  const root = document.documentElement;
  const block = (selector: string) => {
    const start = tokensCss.indexOf(selector);
    const open = tokensCss.indexOf('{', start);
    let depth = 0;
    for (let i = open; i < tokensCss.length; i += 1) {
      if (tokensCss[i] === '{') depth += 1;
      if (tokensCss[i] === '}' && --depth === 0) return tokensCss.slice(open + 1, i);
    }
    return '';
  };
  const vars: Record<string, string> = {};
  for (const source of mode === 'dark' ? [block(':root {'), block("[data-theme='dark']")] : [block(':root {')]) {
    for (const match of source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) vars[match[1]] = match[2].trim();
  }
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
  if (mode === 'dark') root.setAttribute('data-theme', 'dark');
  else root.removeAttribute('data-theme');
}

describe('canvas palette', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style');
    document.documentElement.removeAttribute('data-theme');
  });

  it('only reads tokens that tokens.css declares', () => {
    // A renamed token would silently fall back to the light colour on the canvas, in dark mode too.
    const declared = new Set([...tokensCss.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((match) => match[1]));
    expect(CANVAS_TOKEN_NAMES.filter((name) => !declared.has(name))).toEqual([]);
  });

  it('resolves different colours for light and dark', () => {
    installTokens('light');
    const light = readCanvasPalette();
    installTokens('dark');
    const dark = readCanvasPalette();

    for (const field of ['card', 'page', 'textPrimary', 'action', 'success', 'error', 'borderStrong'] as const) {
      expect(dark[field], field).not.toBe(light[field]);
    }
    expect(light.card.toLowerCase()).toBe('#ffffff');
    expect(dark.card.toLowerCase()).toBe('#121c32');
  });

  it('reads the live document rather than a cached copy', () => {
    installTokens('light');
    expect(readCanvasPalette().textPrimary).toBe('#16213a');
    installTokens('dark');
    expect(readCanvasPalette().textPrimary).toBe('#e6ecf8');
  });

  it('falls back to the light tokens when a variable is missing', () => {
    document.documentElement.removeAttribute('style');
    expect(readCanvasPalette().action).toBe(FALLBACK_PALETTE.action);
  });

  it('keeps a graph card readable: text on card reaches 4.5:1 in both themes', () => {
    const lum = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (a: string, b: string) => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };
    for (const mode of ['light', 'dark'] as const) {
      installTokens(mode);
      const p = readCanvasPalette();
      expect(ratio(p.textPrimary, p.card), `${mode} text on card`).toBeGreaterThanOrEqual(4.5);
      // Status tints carry their own foreground, which is what Plan DAG node labels use.
      for (const [fg, bg] of [[p.success, p.successBg], [p.error, p.errorBg], [p.warning, p.warningBg], [p.action, p.actionBg], [p.blocked, p.blockedBg]]) {
        expect(ratio(fg, bg), `${mode} ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});

describe('withAlpha', () => {
  it('converts hex and rgb colours', () => {
    expect(withAlpha('#ffffff', 0.5)).toBe('rgba(255, 255, 255, 0.5)');
    expect(withAlpha('#abc', 1)).toBe('rgba(170, 187, 204, 1)');
    expect(withAlpha('rgb(10, 20, 30)', 0.25)).toBe('rgba(10, 20, 30, 0.25)');
    expect(withAlpha('rgba(10, 20, 30, 0.9)', 0.25)).toBe('rgba(10, 20, 30, 0.25)');
  });
  it('clamps alpha and passes unknown formats through', () => {
    expect(withAlpha('#000', 3)).toBe('rgba(0, 0, 0, 1)');
    expect(withAlpha('var(--x)', 0.5)).toBe('var(--x)');
  });
});
