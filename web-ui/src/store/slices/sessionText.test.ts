import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLanguageStore } from '@/i18n/language';

vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null, user: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@api/chat', () => ({ chatApi: {} }));

import { createSessionSlice } from './createSessionSlice';
import { createResponseStyleSlice } from './createResponseStyleSlice';

function build() {
  const store: any = { sessions: [], currentSession: null, responsePreferenceSaving: new Set(),
    syncUploadedFilesFromServer: vi.fn().mockResolvedValue(undefined), clearUploadedFiles: vi.fn(), uploadedFiles: [] };
  const get = () => store;
  const set = (updater: any) => Object.assign(store, typeof updater === 'function' ? updater(store) : updater);
  Object.assign(store, createSessionSlice(set as any, get, {} as any), createResponseStyleSlice(set as any, get, {} as any));
  return store;
}

beforeEach(() => useLanguageStore.getState().setLanguage('en'));
afterEach(() => useLanguageStore.getState().setLanguage('zh-CN'));

describe('text the session store writes', () => {
  it('names a new chat in the interface language, with the date in the same language', () => {
    const english = build().startNewSession();
    expect(english.title).toMatch(/^New chat \d{1,2}\/\d{1,2}\/\d{4}, /);
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(build().startNewSession().title).toMatch(/^新会话 \d{4}\/\d{1,2}\/\d{1,2} /);
  });

  it('keeps a title that was given', () => {
    expect(build().startNewSession('My chat').title).toBe('My chat');
  });

  it('explains in English why a reply style cannot be saved for a chat that is gone', async () => {
    await expect(build().updateResponsePreferences('missing', { response_style: 'general', response_length: 'auto' }))
      .rejects.toThrow('This chat no longer exists. Reopen it and try again.');
    useLanguageStore.getState().setLanguage('zh-CN');
    await expect(build().updateResponsePreferences('missing', { response_style: 'general', response_length: 'auto' }))
      .rejects.toThrow('对话已不存在，请重新打开后设置');
  });
});
