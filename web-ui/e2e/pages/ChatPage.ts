import { type Page } from '@playwright/test';

/**
 * Page object model for the Chat page.
 *
 * Encapsulates selectors and interactions for the chat panel rendered by
 * `src/components/chat/ChatPanel.tsx`.
 */
export class ChatPage {
  constructor(private page: Page) {}

  /** Navigate to the chat page. */
  async navigate(): Promise<void> {
    await this.page.goto('/chat');
  }

  /** Return `true` when the message input area is visible. */
  async isLoaded(): Promise<boolean> {
    const input = this.page.locator('.chat-input-area textarea');
    return input.isVisible({ timeout: 10000 }).catch(() => false);
  }

  /**
   * Type a message into the chat input and click the Send button.
   */
  async sendMessage(text: string): Promise<void> {
    const input = this.page.locator('.chat-input-area textarea');
    await input.fill(text);
    await this.page.locator('.chat-input-main button', { hasText: 'Send' }).click();
  }

  /**
   * Return an array of visible message text contents from the message list.
   */
  async getMessages(): Promise<string[]> {
    const container = this.page.locator('.chat-messages');
    await container.waitFor({ state: 'visible', timeout: 10000 });

    // Each ChatMessage component renders inside the .chat-messages container.
    // Collect all direct child text content.
    const messageElements = container.locator('.chat-message-content');
    const count = await messageElements.count();

    const messages: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await messageElements.nth(i).textContent();
      if (text) {
        messages.push(text.trim());
      }
    }
    return messages;
  }

  /**
   * Wait for a new message to appear in the chat (e.g. a system response).
   * Polls the message list until the count increases.
   *
   * @param timeoutMs Maximum time to wait in milliseconds (default 30 000).
   */
  async waitForResponse(timeoutMs = 30000): Promise<void> {
    const container = this.page.locator('.chat-messages');
    const initialCount = await container.locator('.chat-message-content').count();

    await this.page.waitForFunction(
      ({ selector, startCount }) => {
        const el = document.querySelector(selector);
        if (!el) return false;
        return el.querySelectorAll('.chat-message-content').length > startCount;
      },
      { selector: '.chat-messages', startCount: initialCount },
      { timeout: timeoutMs },
    );
  }
}
