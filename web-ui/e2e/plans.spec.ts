import { test, expect } from '@playwright/test';
import { LoginPage } from './pages/LoginPage';
import { PlansPage } from './pages/PlansPage';

/**
 * Plan panel E2E tests.
 *
 * The standalone /plans page no longer exists in the SPA; plan visualization
 * lives in the right-hand tab panel of /chat. These tests validate that the
 * panel renders and its tabs switch.
 */
test.describe('Plan panel', () => {
  let plansPage: PlansPage;

  test.beforeEach(async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.navigate();
    await loginPage.fillEmail('test@example.com');
    await loginPage.fillPassword('password123');
    await loginPage.submit();
    await page.waitForURL('**/chat', { timeout: 15000 });

    plansPage = new PlansPage(page);
    await plansPage.navigate();
  });

  test('plan panel renders with all tabs for logged-in user', async () => {
    expect(await plansPage.isPanelLoaded()).toBe(true);

    const tabs = await plansPage.getTabNames();
    for (const expected of ['Plan', 'Execution Status', 'Artifacts', 'Agent Work']) {
      expect(tabs).toContain(expected);
    }
  });

  test('switching to Execution Status tab shows its panel', async () => {
    await plansPage.openTab('Execution Status');

    const panelText = await plansPage.getActivePanelText();
    // Empty state or content — either way the panel must render something.
    expect(panelText.trim().length).toBeGreaterThan(0);
  });
});
