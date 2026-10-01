import { type Page } from '@playwright/test';

/** Executed in the browser: a user row or optimistic assistant cannot satisfy this gate. */
export function hasCompletedResponseForTurn({ clientMessageId }: { clientMessageId: string }): boolean {
  const rows = Array.from(document.querySelectorAll<HTMLElement>('.message'));
  const userIndex = rows.findIndex((row) => row.classList.contains('user') && row.dataset.clientMessageId === clientMessageId);
  if (userIndex < 0) return false;
  for (const row of rows.slice(userIndex + 1)) {
    if (row.classList.contains('user')) return false;
    if (row.classList.contains('assistant') && row.dataset.messageStatus === 'completed' && Number(row.dataset.responseLength) > 0) return true;
  }
  return false;
}

/**
 * Page object model for the chat panel.
 *
 * Selectors target the current Ant Design + rc-virtual-list implementation:
 * the message input is the only textarea on the page (identified by its
 * placeholder), and messages render as `.message` rows (user rows carry
 * `.user`) with `.message-bubble` content inside a virtualized list.
 */
export class ChatPage {
  private clientMessageId: string | null = null;
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
    const previousIds = await this.page.locator('.message.user').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-client-message-id')));
    const input = this.page.getByRole('textbox', { name: /输入消息/ });
    await input.fill(text);
    await this.page.locator('button', { hasText: 'Send' }).first().click();
    await this.page.waitForFunction(({ text, previousIds }) =>
      Array.from(document.querySelectorAll<HTMLElement>('.message.user')).some((row) =>
        row.dataset.clientMessageId && !previousIds.includes(row.dataset.clientMessageId) && row.querySelector('.message-bubble')?.textContent?.includes(text)),
    { text, previousIds }, { timeout: 15000 });
    this.clientMessageId = await this.page.locator('.message.user', { hasText: text }).last().getAttribute('data-client-message-id');
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
   * Wait for this turn's nonempty, completed assistant response.
   */
  async waitForResponse(timeoutMs = 30000): Promise<void> {
    if (!this.clientMessageId) throw new Error('Send a message before waiting for its response.');
    await this.page.waitForFunction(hasCompletedResponseForTurn, { clientMessageId: this.clientMessageId }, { timeout: timeoutMs });
  }
}
