import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Wordmark, { PRODUCT_NAME } from './Wordmark';

describe('Wordmark', () => {
  it('reads as one name and splits into two colour spans', () => {
    const { container } = render(<Wordmark />);
    expect(container.textContent).toBe(PRODUCT_NAME);
    expect(container.querySelector('.wordmark-phage')?.textContent).toBe('Phage-');
    expect(container.querySelector('.wordmark-agent')?.textContent).toBe('Agent');
  });

  it('takes its two colours from different theme tokens', () => {
    const css = readFileSync(resolve(__dirname, '../../styles/index.css'), 'utf8');
    expect(css).toMatch(/\.wordmark-phage\s*\{\s*color:\s*var\(--color-brand\)/);
    expect(css).toMatch(/\.wordmark-agent\s*\{\s*color:\s*var\(--color-action\)/);
  });
});

describe('product name', () => {
  const root = resolve(__dirname, '../..');
  const files = (dir: string): string[] =>
    readdirSync(dir).flatMap((entry) => {
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) return files(path);
      return /\.(tsx?|css)$/.test(path) && !/\.test\.tsx?$/.test(path) ? [path] : [];
    });

  it('is Phage-Agent everywhere a user can read it', () => {
    // The lowercase `gagent.*` localStorage keys are storage identifiers, not the name, and stay.
    const stale = files(root).filter((file) => /GAgent/.test(readFileSync(file, 'utf8')));
    expect(stale.map((file) => relative(root, file))).toEqual([]);
    expect(readFileSync(resolve(root, '../index.html'), 'utf8')).not.toMatch(/GAgent/);
  });
});
