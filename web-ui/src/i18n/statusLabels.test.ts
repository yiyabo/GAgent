import { describe, expect, it } from 'vitest';
import en from './locales/en.json';
import zhCN from './locales/zh-CN.json';
import { statusLabel } from './statusLabels';
import { translate } from './useT';
import { useLanguageStore } from './language';

describe('statusLabel', () => {
  it('has a catalogue entry for every status the backend uses, in both languages', () => {
    // A key built from a status string is not checked by the compiler, so this is the check.
    for (const status of ['pending', 'running', 'completed', 'failed', 'blocked', 'skipped', 'needs_review']) {
      const shown = statusLabel(translate, status);
      expect(shown, `no label for ${status}`).not.toBe(status);
      expect(shown).not.toMatch(/^status\./);
    }
    expect(Object.keys(en.status).sort()).toEqual(Object.keys(zhCN.status).sort());
  });

  it('translates with the language and understands the old aliases', () => {
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(statusLabel(translate, 'completed')).toBe('已完成');
    expect(statusLabel(translate, 'done')).toBe('已完成');
    expect(statusLabel(translate, 'executing')).toBe('运行中');
    expect(statusLabel(translate, 'needs_review')).toBe('待复核');
    useLanguageStore.getState().setLanguage('en');
    expect(statusLabel(translate, 'needs_review')).toBe('Needs review');
    expect(statusLabel(translate, undefined)).toBe('Pending');
    useLanguageStore.getState().setLanguage('zh-CN');
  });

  it('shows an unknown status as it came instead of a blank or a raw key', () => {
    expect(statusLabel(translate, 'quarantined')).toBe('quarantined');
  });
});

describe('keys assembled from a value at runtime', () => {
  // The compiler cannot check `t(\`quality.level.${level}\`)`; if a catalogue entry is missing the page
  // shows a raw key. These are all the places that build a key from data, with every value they can take.
  const cases: [string, string[]][] = [
    ['quality.level.', ['satisfied', 'acceptable', 'negative', 'angry']],
    ['quality.window', ['24', '168', '720']],
    ['memory.type.', ['conversation', 'experience', 'knowledge', 'context']],
    ['memory.importance.', ['critical', 'high', 'medium', 'low', 'temporary']],
  ];
  it('every value has a catalogue entry in both languages', () => {
    const lookup = (catalogue: unknown, path: string) => path.split('.').reduce<any>((node, key) => node?.[key], catalogue);
    for (const [prefix, values] of cases) {
      for (const value of values) {
        const key = `${prefix}${value}`;
        expect(typeof lookup(en, key), `en ${key}`).toBe('string');
        expect(typeof lookup(zhCN, key), `zh-CN ${key}`).toBe('string');
      }
    }
  });
});
