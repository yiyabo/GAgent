import { test, expect, type Page } from '@playwright/test';
import { LoginPage } from './pages/LoginPage';
import { ChatPage } from './pages/ChatPage';

/**
 * Chat interaction E2E tests.
 *
 * Validates Requirements: 10.1, 10.2, 10.3
 */
test.describe('Chat interaction', () => {
  let chatPage: ChatPage;

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

    chatPage = new ChatPage(page);
  });

  test('chat interface loads with message input area', async () => {
    const loaded = await chatPage.isLoaded();
    expect(loaded).toBe(true);
  });

  test('sent message appears in message list', async () => {
    const testMessage = 'Hello, this is a test message';
    await chatPage.sendMessage(testMessage);

    // The sent message should appear in the message list
    const messages = await chatPage.getMessages();
    const found = messages.some((msg) => msg.includes(testMessage));
    expect(found).toBe(true);
  });

  test('system response appears within 30 seconds', async () => {
    const testMessage = 'What can you help me with?';
    await chatPage.sendMessage(testMessage);

    // Wait for the system to respond (new message count increases)
    await chatPage.waitForResponse(30000);

    const messages = await chatPage.getMessages();
    // There should be at least 2 messages: the user message and the system response
    expect(messages.length).toBeGreaterThanOrEqual(2);
  });
});
