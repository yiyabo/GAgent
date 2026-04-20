import { test, expect, type Page } from '@playwright/test';
import { LoginPage } from './pages/LoginPage';

/**
 * Login flow E2E tests.
 *
 * Validates Requirements: 9.1, 9.2, 9.3
 */
test.describe('Login flow', () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
    await loginPage.navigate();
  });

  test('valid credentials navigates to /chat', async ({ page }) => {
    await loginPage.fillEmail('test@example.com');
    await loginPage.fillPassword('password123');
    await loginPage.submit();

    // After successful login the app should redirect to /chat
    await page.waitForURL('**/chat', { timeout: 15000 });
    expect(page.url()).toContain('/chat');
  });

  test('invalid credentials shows error alert', async () => {
    await loginPage.fillEmail('invalid@example.com');
    await loginPage.fillPassword('wrongpassword');
    await loginPage.submit();

    // An Ant Design error alert should appear
    const errorText = await loginPage.getErrorAlert();
    expect(errorText).not.toBeNull();
    expect(await loginPage.isOnLoginPage()).toBe(true);
  });

  test('unauthenticated user on protected route redirects to /login', async ({ page }) => {
    // Navigate directly to a protected route without logging in
    await page.goto('/chat');

    // Should be redirected to the login page
    await page.waitForURL('**/login**', { timeout: 15000 });
    expect(page.url()).toContain('/login');
  });
});
