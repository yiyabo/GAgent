import { expect, test } from '@playwright/test';
import { mockApi } from './mockApi';

/**
 * The language switch, in a real browser against the production build.
 *
 * The build under test uses the default of the deployment this repo is deployed to first (CityU:
 * English), so a fresh visitor sees English. The .8 default (zh-CN) is covered by the unit tests
 * that exercise `deploymentDefault`; building twice just for that would double the CI time.
 */
test.describe('UI language', () => {
  test('a fresh visitor gets the deployment default, and <html lang> and the title agree', async ({ page }) => {
    await mockApi(page);
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    const lang = await page.evaluate(() => document.documentElement.lang);
    expect(['en', 'zh-CN']).toContain(lang);
    await expect(page.getByRole('button', { name: lang === 'en' ? 'Extensions' : '扩展' })).toBeVisible();
    expect(await page.title()).toContain(lang === 'en' ? 'Research workspace' : '研究工作台');
  });

  test('the switcher changes the header, the title and <html lang> at once, and the choice survives a reload', async ({ page }) => {
    await mockApi(page);
    // Seed English once. An init script would re-run on reload and overwrite the choice under test.
    await page.addInitScript(() => {
      if (!window.localStorage.getItem('gagent.language')) window.localStorage.setItem('gagent.language', 'en');
    });
    await page.goto('/chat');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('button', { name: 'Extensions' })).toBeVisible();

    await page.getByRole('button', { name: 'Language' }).click();
    await page.getByRole('menuitem', { name: '中文' }).click();
    await expect(page.getByRole('button', { name: '扩展' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.lang)).toBe('zh-CN');
    expect(await page.evaluate(() => window.localStorage.getItem('gagent.language'))).toBe('zh-CN');

    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('button', { name: '扩展' })).toBeVisible();
    expect(await page.title()).toContain('研究工作台');
  });

  test('antd component text follows the language (the table pager is antd, not ours)', async ({ page }) => {
    await mockApi(page);
    const pagerText = async (saved: 'en' | 'zh-CN') => {
      await page.addInitScript((value) => window.localStorage.setItem('gagent.language', value), saved);
      await page.goto('/chat');
      await page.waitForLoadState('networkidle');
      await page.evaluate(() => {
        window.history.pushState({}, '', '/memory');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      await page.locator('.ant-pagination').first().waitFor();
      return (await page.locator('.ant-pagination').first().innerText()).replace(/\s+/g, ' ');
    };
    expect(await pagerText('en')).toMatch(/\/ page/);
    expect(await pagerText('zh-CN')).toMatch(/条\/页/);
  });
});
