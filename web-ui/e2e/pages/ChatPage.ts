import { type Page } from '@playwright/test';

/**
 * Page object model for the chat panel.
 *
 * Selectors target the current Ant Design + rc-virtual-list implementation:
 * the message input is the only textarea on the page (identified by its
 * placeholder), and messages render as `.message` rows (user rows carry
 * `.user`) with `.message-bubble` content inside a virtualized list.
 */
export class ChatPage {
  constructor(private page: Page) {}

  /** Navigate to the chat page. */
  async navigate(): Promise<void> {
    await this.page.goto('/chat');
  }

  /** Return `true` when the message input area is visible. */
  async isLoaded(): Promise<boolean> {
    const input = this.page.getByRole('textbox', { name: /输入消息/ });
    try {
      await input.waitFor({ state: 'visible', timeout: 45000 });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Type a message into the chat input and click the Send button.
   * The Send button stays disabled until the input has text.
   */
  async sendMessage(text: string): Promise<void> {
    const input = this.page.getByRole('textbox', { name: /输入消息/ });
    await input.fill(text);
    await this.page.locator('button', { hasText: 'Send' }).first().click();
    await this.page
      .locator('.message-bubble', { hasText: text })
      .first()
      .waitFor({ state: 'visible', timeout: 15000 });
  }

  /** Return text content of all rendered message bubbles. */
  async getMessages(): Promise<string[]> {
    return this.page.locator('.message-bubble').allInnerTexts();
  }

  /** Number of currently rendered message rows. */
  async getMessageCount(): Promise<number> {
    return this.page.locator('.message').count();
  }

  /**
   * Wait until more than `initialCount` message rows are rendered, up to
   * `timeout` ms. Used to await the assistant's streamed reply.
   */
  async waitForResponse(timeoutMs = 30000, initialCount = 0): Promise<void> {
    await this.page.waitForFunction(
      (start) => document.querySelectorAll('.message').length > start,
      initialCount,
      { timeout: timeoutMs },
    );
  }
}
