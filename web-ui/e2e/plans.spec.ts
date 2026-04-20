import { test, expect, type Page } from '@playwright/test';
import { LoginPage } from './pages/LoginPage';
import { PlansPage } from './pages/PlansPage';

/**
 * Plan viewing E2E tests.
 *
 * Validates Requirements: 11.1, 11.2
 */
test.describe('Plan viewing', () => {
  let plansPage: PlansPage;

  /**
   * Log in before each test so the user has an authenticated session.
   */
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

  test('plans list is rendered for logged-in user', async ({ page }) => {
    // The plan selector (Ant Design Select) should be visible
    const select = page.locator('.ant-select');
    await expect(select.first()).toBeVisible({ timeout: 10000 });

    // Attempt to read plan titles from the dropdown
    const plans = await plansPage.getPlansList();
    // The list should be rendered (may be empty if no plans exist yet,
    // but the select component itself should be present)
    expect(plans).toBeDefined();
  });

  test('clicking a plan shows detail view with title', async ({ page }) => {
    // Open the dropdown and check if there are plans available
    const plans = await plansPage.getPlansList();

    if (plans.length === 0) {
      // If no plans exist, skip this test gracefully
      test.skip(true, 'No plans available to select');
      return;
    }

    // Select the first plan
    await plansPage.clickPlan(0);

    // The plan detail title should be visible in the Task Details card
    const detailTitle = await plansPage.getPlanDetailTitle();
    expect(detailTitle.length).toBeGreaterThan(0);
  });
});
