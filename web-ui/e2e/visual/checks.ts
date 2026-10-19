import type { Page } from '@playwright/test';

/** One finding from a page scan. */
export interface Finding {
  kind: 'low-contrast' | 'light-block-in-dark';
  detail: string;
}

/**
 * Scans the rendered page. Runs in the browser, so it sees what the user sees: computed colours
 * after every stylesheet, antd token and inline style has applied. Static tests cannot do that.
 *
 *  - low-contrast: a text node whose colour against its nearest opaque background is under `minRatio`.
 *  - light-block-in-dark: a box wider than a button with a near-white background while the theme is
 *    dark (the white chat input and the white tables were exactly this).
 *
 * Skipped on purpose: disabled controls (exempt from contrast rules), canvas/svg/img/iframe content,
 * and elements that are not painted.
 */
export async function scanPage(page: Page, mode: 'light' | 'dark', minRatio = 3): Promise<Finding[]> {
  return page.evaluate(
    ({ mode, minRatio }) => {
      type Rgb = { r: number; g: number; b: number; a: number };
      const parse = (c: string): Rgb | null => {
        const m = c.match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const p = m[1].split(',').map((v) => parseFloat(v));
        return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
      };
      const lin = (v: number) => {
        const x = v / 255;
        return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      };
      const lum = (c: Rgb) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
      const pageBg = parse(getComputedStyle(document.body).backgroundColor) ?? { r: 255, g: 255, b: 255, a: 1 };
      const bgOf = (el: Element): Rgb => {
        for (let e: Element | null = el; e; e = e.parentElement) {
          const b = parse(getComputedStyle(e).backgroundColor);
          if (b && b.a > 0.9) return b;
        }
        return pageBg;
      };
      const label = (el: Element) => `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className || '').split(/\s+/).filter((c) => !c.startsWith('css-')).slice(0, 2).join('.')}`;

      const found: { kind: 'low-contrast' | 'light-block-in-dark'; detail: string }[] = [];
      const seen = new Set<string>();

      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = (n.textContent ?? '').trim();
        const el = n.parentElement;
        if (!text || !el) continue;
        if (el.closest('canvas,svg,img,iframe,script,style,[disabled],[aria-disabled="true"],.ant-btn-disabled,.ant-select-disabled,.ant-input-disabled')) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue;
        const box = el.getBoundingClientRect();
        if (box.width === 0 || box.height === 0) continue;
        // Gradient-clipped text paints with the background, not `color`; measure what is drawn instead.
        if (cs.webkitTextFillColor === 'rgba(0, 0, 0, 0)') continue;
        const fg = parse(cs.color);
        if (!fg) continue;
        const bg = bgOf(el);
        const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
        const ratio = (hi + 0.05) / (lo + 0.05);
        if (ratio < minRatio) {
          const key = `c:${label(el)}:${text.slice(0, 24)}`;
          if (!seen.has(key)) {
            seen.add(key);
            found.push({ kind: 'low-contrast', detail: `${ratio.toFixed(2)}:1 "${text.slice(0, 32)}" ${label(el)}` });
          }
        }
      }

      if (mode === 'dark') {
        for (const el of Array.from(document.querySelectorAll('body *'))) {
          if (el.closest('canvas,svg,img,iframe')) continue;
          const box = el.getBoundingClientRect();
          if (box.width < 120 || box.height < 24) continue;
          const cs = getComputedStyle(el);
          if (cs.visibility === 'hidden' || cs.display === 'none') continue;
          const bg = parse(cs.backgroundColor);
          if (!bg || bg.a < 0.5) continue;
          if (lum(bg) > 0.45) found.push({ kind: 'light-block-in-dark', detail: `${label(el)} ${cs.backgroundColor} ${Math.round(box.width)}x${Math.round(box.height)}` });
        }
      }
      return found.slice(0, 25);
    },
    { mode, minRatio },
  );
}
