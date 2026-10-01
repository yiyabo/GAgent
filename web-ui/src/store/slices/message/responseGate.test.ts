import { afterEach, describe, expect, it } from 'vitest';
import { hasCompletedResponseForTurn } from '../../../../e2e/pages/ChatPage';

afterEach(() => { document.body.innerHTML = ''; });

describe('E2E current-turn response gate', () => {
  it('rejects old answers, a sent user row and an optimistic empty assistant', () => {
    document.body.innerHTML = '<div class="message user" data-client-message-id="old"></div><div class="message assistant" data-message-status="completed" data-response-length="100"></div><div class="message user" data-client-message-id="new"></div><div class="message assistant" data-message-status="pending" data-response-length="0"></div>';
    expect(hasCompletedResponseForTurn({ clientMessageId: 'new' })).toBe(false);
    const assistant = document.querySelectorAll<HTMLElement>('.message.assistant')[1];
    assistant.dataset.messageStatus = 'completed';
    expect(hasCompletedResponseForTurn({ clientMessageId: 'new' })).toBe(false);
    assistant.dataset.responseLength = '2';
    expect(hasCompletedResponseForTurn({ clientMessageId: 'new' })).toBe(true);
  });

  it('does not accept the next turn or a failed assistant as the requested response', () => {
    document.body.innerHTML = '<div class="message user" data-client-message-id="new"></div><div class="message assistant" data-message-status="failed" data-response-length="20"></div><div class="message user" data-client-message-id="next"></div><div class="message assistant" data-message-status="completed" data-response-length="20"></div>';
    expect(hasCompletedResponseForTurn({ clientMessageId: 'new' })).toBe(false);
    expect(hasCompletedResponseForTurn({ clientMessageId: 'next' })).toBe(true);
  });
});
