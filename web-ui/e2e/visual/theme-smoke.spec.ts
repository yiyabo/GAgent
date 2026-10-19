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
