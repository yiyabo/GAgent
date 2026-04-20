import { type Page } from '@playwright/test';

/**
 * Page object model for the Login page.
 *
 * Encapsulates selectors and interactions for the Ant Design login form
 * rendered by `src/pages/Login.tsx`.
 */
export class LoginPage {
  constructor(private page: Page) {}

  /** Navigate to the login page. */
  async navigate(): Promise<void> {
    await this.page.goto('/login');
  }

  /** Fill the email input (Ant Design Form.Item generates `id` from `name`). */
  async fillEmail(email: string): Promise<void> {
    await this.page.fill('#email', email);
  }

  /** Fill the password input. */
  async fillPassword(password: string): Promise<void> {
    await this.page.fill('#password', password);
  }

  /** Click the submit button. */
  async submit(): Promise<void> {
    await this.page.click('button[type="submit"]');
  }

  /**
   * Return the text content of the Ant Design error alert, or `null` if no
   * error alert is visible.
   */
  async getErrorAlert(): Promise<string | null> {
    const alert = this.page.locator('.ant-alert-error');
    if (await alert.isVisible({ timeout: 5000 }).catch(() => false)) {
      return alert.textContent();
    }
    return null;
  }

  /** Check whether the current URL contains `/login`. */
  async isOnLoginPage(): Promise<boolean> {
    return this.page.url().includes('/login');
  }
}
