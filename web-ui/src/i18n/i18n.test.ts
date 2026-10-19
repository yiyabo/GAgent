import { beforeEach, describe, expect, it } from 'vitest';
import en from './locales/en.json';
import zhCN from './locales/zh-CN.json';
import i18n from './index';
import {
  LANGUAGE_STORAGE_KEY,
  deploymentDefault,
  readStoredLanguage,
  resolveLanguage,
  useLanguageStore,
} from './language';
import { translate } from './useT';

/** Every dotted path in a catalogue. */
function paths(node: unknown, prefix = ''): string[] {
  if (typeof node === 'string') return [prefix];
  return Object.entries(node as Record<string, unknown>).flatMap(([key, value]) => paths(value, prefix ? `${prefix}.${key}` : key));
}

/** `{{name}}` placeholders in a string, sorted. */
const placeholders = (text: string): string[] => [...text.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]).sort();

const lookup = (catalogue: unknown, path: string): string =>
  path.split('.').reduce<any>((node, key) => node?.[key], catalogue);

describe('the two catalogues stay in step', () => {
  it('have exactly the same keys', () => {
    const english = paths(en).sort();
    const chinese = paths(zhCN).sort();
    expect(chinese.filter((key) => !english.includes(key)), 'in zh-CN but not en').toEqual([]);
    expect(english.filter((key) => !chinese.includes(key)), 'in en but not zh-CN').toEqual([]);
  });

  it('never leave a value empty', () => {
    for (const [name, catalogue] of [['en', en], ['zh-CN', zhCN]] as const) {
      const empty = paths(catalogue).filter((key) => lookup(catalogue, key).trim() === '');
      expect(empty, `${name} has empty values`).toEqual([]);
    }
  });

  it('use the same {{placeholders}} in both languages', () => {
    for (const key of paths(en)) {
      expect(placeholders(lookup(zhCN, key)), key).toEqual(placeholders(lookup(en, key)));
    }
  });

  it('translate every value: an identical string is an untranslated copy', () => {
    // Only the brand and strings that are the same word in both languages may match.
    const ALLOWED_SAME = ['app.name', 'header.homeAria'];
    const same = paths(en).filter((key) => lookup(en, key) === lookup(zhCN, key) && !ALLOWED_SAME.includes(key));
    expect(same).toEqual([]);
  });
});

describe('language choice', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useLanguageStore.setState({ language: 'en' });
  });

  it('takes the deployment default when nothing is stored, and English when the setting is missing or wrong', () => {
    expect(deploymentDefault('zh-CN')).toBe('zh-CN');
    expect(deploymentDefault('en')).toBe('en');
    expect(deploymentDefault(undefined)).toBe('en');
    expect(deploymentDefault('fr')).toBe('en');
    expect(resolveLanguage(null, 'zh-CN')).toBe('zh-CN');
  });

  it('prefers a stored choice over the deployment default, and ignores garbage', () => {
    expect(resolveLanguage('en', 'zh-CN')).toBe('en');
    expect(readStoredLanguage({ getItem: () => 'klingon' })).toBeNull();
    expect(readStoredLanguage({ getItem: () => { throw new Error('blocked'); } })).toBeNull();
    expect(readStoredLanguage({ getItem: () => 'zh-CN' })).toBe('zh-CN');
  });

  it('remembers a choice and switches i18next with it', () => {
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh-CN');
    expect(i18n.language).toBe('zh-CN');
    expect(translate('common.cancel')).toBe('取消');
    useLanguageStore.getState().setLanguage('en');
    expect(translate('common.cancel')).toBe('Cancel');
  });
});

// ---------------------------------------------------------------------------------------------
// Migration ratchet: user-visible text must go through t(), and the debt may only shrink.
// ---------------------------------------------------------------------------------------------
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { HARDCODED_BASELINE } from './hardcodedBaseline';

const SRC = resolve(__dirname, '..');
const CJK_RE = /[一-鿿]/;

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return tsxFiles(path);
    return path.endsWith('.tsx') && !path.endsWith('.test.tsx') ? [path] : [];
  });
}

const stripComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\])\/\/[^\n]*/g, '$1');

const JSX_TEXT = />\s*([^<>{}\n]*[A-Za-z一-鿿][^<>{}\n]*?)\s*</g;
const ATTRS = /\b(?:title|label|placeholder|tip|description|okText|cancelText|aria-label|alt|text|emptyText|message)\s*=\s*(["'])((?:(?!\1).)*[A-Za-z一-鿿](?:(?!\1).)*)\1/g;
const CALLS = /\b(?:message|notification|Modal)\.(?:success|error|warning|info|loading|confirm)\(\s*([`'"])((?:(?!\1).)*[A-Za-z一-鿿](?:(?!\1).)*)\1/g;
const PROPS = /\b(?:title|label|placeholder|tip|description|text|header|desc|emptyText)\s*:\s*(['"`])((?:(?!\1).)*[A-Za-z一-鿿](?:(?!\1).)*)\1/g;

function looksLikeText(raw: string): boolean {
  const text = raw.trim();
  if (CJK_RE.test(text)) return true;
  if (!/[A-Za-z]{2,}/.test(text)) return false;
  if (/^[a-z0-9_\-./:#@[\]()=<>$%{}]+$/.test(text)) return false; // identifiers, class names, paths
  if (/^(var\(|rgb|#|https?:|\/|\.|calc\()/.test(text)) return false;
  if (/^[a-z]+([A-Z][a-z]+)+$/.test(text)) return false; // camelCase ids
  return /^[A-Z一-鿿]/.test(text) || text.includes(' ');
}

/** Pieces of visible text in a component that do not go through t(). */
export function hardcodedText(source: string): string[] {
  const code = stripComments(source);
  const found: string[] = [];
  for (const rx of [JSX_TEXT, ATTRS, CALLS, PROPS]) {
    for (const match of code.matchAll(rx)) {
      const value = match[match.length - 1 === 1 ? 1 : 2] ?? match[1];
      const text = (rx === JSX_TEXT ? match[1] : match[2]) ?? value;
      if (/\bt\(\s*['"`]/.test(text)) continue;
      if (looksLikeText(text)) found.push(text.trim().slice(0, 50));
    }
  }
  return found;
}

describe('hard-coded UI text only shrinks', () => {
  const files = tsxFiles(SRC).map((file) => [relative(SRC, file), readFileSync(file, 'utf8')] as const);

  it('no file gains untranslated text, and a file not in the baseline has none', () => {
    const over = files
      .map(([name, source]) => ({ name, count: hardcodedText(source).length, allowed: HARDCODED_BASELINE[name] ?? 0 }))
      .filter((row) => row.count > row.allowed)
      .map((row) => `${row.name}: ${row.count} hard-coded, ${row.allowed} allowed`);
    expect(over, 'wrap the new text in t() and add the key to both locale files').toEqual([]);
  });

  it('keeps the baseline honest: a migrated file must be removed from it', () => {
    const stale = files
      .map(([name, source]) => ({ name, count: hardcodedText(source).length, allowed: HARDCODED_BASELINE[name] }))
      .filter((row) => row.allowed !== undefined && row.count < row.allowed)
      .map((row) => `${row.name}: baseline ${row.allowed}, now ${row.count}`);
    expect(stale, 'lower (or delete) the number in hardcodedBaseline.ts').toEqual([]);
  });
});
