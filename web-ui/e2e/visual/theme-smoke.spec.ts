import { expect, test } from '@playwright/test';
import { scanPage } from './checks';
import { mockApi } from './mockApi';

/**
 * Visual smoke: every page, in both themes, rendered by a real browser.
 *
 * Fails when text drops below 3:1 contrast, when a light slab shows up in the dark theme, or when
 * the page throws. These are the regressions that unit tests cannot see because they only exist
 * after CSS, antd tokens and inline styles have all been applied. The chat input going white in dark
 * mode is the case that motivated it.
 *
 * Threshold is 3:1 (WCAG large-text / UI level), deliberately below the 4.5:1 that theme.test.ts
 * enforces on the token values: this check measures the rendered page, where antd's own adornments
 * (placeholders, disabled states) legitimately sit lower.
 */
const ROUTES = ['/chat', '/plans', '/memory', '/dashboard', '/tasks', '/analytics', '/extensions'] as const;

for (const mode of ['light', 'dark'] as const) {
  test.describe(`${mode} theme`, () => {
    for (const route of ROUTES) {
      test(`${route} has readable text${mode === 'dark' ? ' and no light slabs' : ''}`, async ({ page }) => {
        const errors: string[] = [];
        page.on('pageerror', (error) => errors.push(error.message));
        const report = await mockApi(page);

        await page.addInitScript((value) => window.localStorage.setItem('gagent.theme', value), mode);
        await page.goto('/chat');
        await page.waitForLoadState('networkidle');
        if (route !== '/chat') {
          // The app redirects a cold load of other routes to /chat; navigate in-app, as a user would.
          await page.evaluate((to) => {
            window.history.pushState({}, '', to);
            window.dispatchEvent(new PopStateEvent('popstate'));
          }, route);
        }
        if (route === '/plans') {
          await page.locator('.ant-select').first().click();
          await page.locator('.ant-select-item-option').first().click();
        }
        if (route === '/memory') await page.getByRole('tab', { name: /Graph/ }).click();
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(800);

        expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme') ?? 'light')).toBe(mode);

        const findings = await scanPage(page, mode);
        if (report.unmocked.length) console.log(`[${mode} ${route}] calls without a fixture: ${report.unmocked.join(', ')}`);
        expect(errors, 'uncaught page errors').toEqual([]);
        expect(findings.map((f) => `${f.kind}: ${f.detail}`)).toEqual([]);
      });
    }
  });
}

/**
 * The relationship graph's full-screen card view is drawn on a <canvas>, which text scans cannot
 * read. This checks the DOM around it (legend, stats bar, toolbar) and that the canvas actually
 * painted something that follows the theme.
 */
for (const mode of ['light', 'dark'] as const) {
  test(`${mode}: 3D relationship view paints with theme colours`, async ({ page }) => {
    await mockApi(page);
    await page.addInitScript((value) => window.localStorage.setItem('gagent.theme', value), mode);
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    await page.getByRole('tab', { name: /关系图|Graph/ }).click();
    await page.getByRole('button', { name: /3D 全屏|3D fullscreen/ }).click();
    await page.waitForSelector('canvas');
    await page.waitForTimeout(1500);

    const findings = await scanPage(page, mode);
    expect(findings.map((f) => `${f.kind}: ${f.detail}`)).toEqual([]);

    // Sample the canvas: its card faces must be the theme's card colour, not a fixed cream.
    const sample = await page.evaluate(() => {
      const canvas = document.querySelector('canvas') as HTMLCanvasElement;
      const ctx = canvas.getContext('2d')!;
      const counts = new Map<string, number>();
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < data.length; i += 4 * 97) {
        if (data[i + 3] < 250) continue;
        const key = `${data[i]},${data[i + 1]},${data[i + 2]}`;
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
      return top ? top[0].split(',').map(Number) : [];
    });
    expect(sample.length, 'the canvas painted nothing').toBe(3);
    const luminance = (0.2126 * sample[0] + 0.7152 * sample[1] + 0.0722 * sample[2]) / 255;
    if (mode === 'dark') expect(luminance, `dominant canvas colour ${sample} is too bright for dark`).toBeLessThan(0.3);
    else expect(luminance, `dominant canvas colour ${sample} is too dark for light`).toBeGreaterThan(0.7);
  });
}
