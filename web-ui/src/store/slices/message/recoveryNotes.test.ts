import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatSession } from '@/types';

vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@api/chat', () => ({ chatApi: {
  getHistory: vi.fn(), getActiveRun: vi.fn(), updateSession: vi.fn(), getActionStatus: vi.fn(), autotitleSession: vi.fn(),
  getResumeInfo: vi.fn(),
} }));

import { chatApi } from '@api/chat';
import { isRecoveryNote, resetAutoResumeGuardsForTest } from './recovery';
import { createSessionSlice } from '../createSessionSlice';
import { createUISlice } from '../createUISlice';
import { _looksLikeSubstantialAssistant, _recoverAfterStreamFailure, createMessageSlice } from './index';
import { useLanguageStore } from '@/i18n/language';
import { translate } from '@/i18n/useT';
import { resolveRequestFailureMessage } from '@/components/chat/message/utils';

const session = (): ChatSession => ({
  id: 'local-A', session_id: 'server-A', title: 'A',
  created_at: new Date(), updated_at: new Date(), isUserNamed: true, plan_id: null, plan_title: null,
  messages: [
    { id: 'user-A', type: 'user', content: 'Request A', timestamp: new Date(), metadata: { client_message_id: 'turn-A' } },
    { id: 'assistant-A', type: 'assistant', content: '', timestamp: new Date(), metadata: { status: 'pending', chat_run_id: 'run-A', client_message_id: 'turn-A' } },
  ],
});

function buildStore() {
  const a = session();
  const store: any = {
    sessions: [a], currentSession: null,
    syncUploadedFilesFromServer: vi.fn().mockResolvedValue(undefined), clearUploadedFiles: vi.fn(),
    uploadedFiles: [], memoryEnabled: false, relevantMemories: [],
  };
  const get = () => store;
  const set = (updater: any) => Object.assign(store, typeof updater === 'function' ? updater(store) : updater);
  Object.assign(store, createSessionSlice(set as any, get, {} as any), createMessageSlice(set as any, get, {} as any), createUISlice(set as any, get, {} as any));
  store.sessions = [a];
  store.setCurrentSession(a);
  store.setActiveRunId('server-A', 'run-A');
  store.setSessionProcessing('server-A', true);
  store.resumeChatRun = vi.fn().mockResolvedValue(undefined);
  return { store, get, set, a };
}

const assistant = (store: any) => store.sessions[0].messages.find((message: any) => message.id === 'assistant-A');

beforeEach(() => {
  useLanguageStore.getState().setLanguage('zh-CN');
  resetAutoResumeGuardsForTest();
  vi.clearAllMocks();
  vi.mocked(chatApi.getHistory).mockResolvedValue({ data: { success: true, messages: [], has_more: false } } as any);
  vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [] } } as any);
  vi.mocked(chatApi.updateSession).mockResolvedValue({} as any);
  vi.mocked(chatApi.getResumeInfo).mockResolvedValue({ can_resume: true, automatic_resume_allowed: true } as any);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const REAL_ANSWER = 'The correlation between host range and receptor-binding protein is shown in the table below.';

describe('a partial reply is kept only when it is real content', () => {
  it('keeps a real partial answer', () => {
    expect(_looksLikeSubstantialAssistant(REAL_ANSWER)).toBe(true);
  });

  it('drops short text, errors, and the interruption notes', () => {
    expect(_looksLikeSubstantialAssistant('ok')).toBe(false);
    expect(_looksLikeSubstantialAssistant('Request failed. Please check: the backend, the model key and the network, then retry.')).toBe(false);
  });

  it('drops a status note the store wrote, in either language, but not real text that merely resembles one', () => {
    for (const language of ['zh-CN', 'en'] as const) {
      useLanguageStore.getState().setLanguage(language);
      const note = `${translate('chat.recovery.interrupted')} ${translate('chat.recovery.failureTail')}`;
      expect(_looksLikeSubstantialAssistant(note, { recovery_note_text: note })).toBe(false);
      // Without the stored note the same words are ordinary text; real text replacing the note ends the match.
      expect(_looksLikeSubstantialAssistant(note, {})).toBe(true);
      expect(_looksLikeSubstantialAssistant(REAL_ANSWER, { recovery_note_text: note })).toBe(true);
    }
  });
});

describe('a failure notice the store wrote is never kept as a partial reply', () => {
  it('in either language, once it is stored beside the content', () => {
    for (const language of ['zh-CN', 'en'] as const) {
      useLanguageStore.getState().setLanguage(language);
      const notice = `${resolveRequestFailureMessage(new Error('unexpected failure'))}\n\n${translate('chat.recovery.failureTail')}`;
      expect(notice.length).toBeGreaterThan(40);
      expect(_looksLikeSubstantialAssistant(notice, { recovery_note_text: notice })).toBe(false);
    }
  });
});

describe('the interruption note shown while a turn is recovering', () => {
  it('is recognised as a note only when it equals the note stored beside it', () => {
    const note = translate('chat.recovery.reconnecting');
    expect(isRecoveryNote(note, { recovery_note_text: note })).toBe(true);
    expect(isRecoveryNote(`  ${note}\n`, { recovery_note_text: note })).toBe(true);
    expect(isRecoveryNote(note, {})).toBe(false);
    expect(isRecoveryNote(note, null)).toBe(false);
    expect(isRecoveryNote(REAL_ANSWER, { recovery_note_text: note })).toBe(false);
    expect(isRecoveryNote('', { recovery_note_text: '' })).toBe(false);
  });

  it('is what a turn without any streamed text shows first, and is not kept as a partial reply', () => {
    const fixture = buildStore();
    void recoverAfterStreamFailure(fixture, null, '');
    const shown = assistant(fixture.store).content as string;
    expect(shown.length).toBeGreaterThan(0);
    const metadata = assistant(fixture.store).metadata;
    expect(isRecoveryNote(shown, metadata)).toBe(true);
    expect(_looksLikeSubstantialAssistant(shown, metadata)).toBe(false);
    expect(metadata).toMatchObject({ status: 'pending', recovering: true });
  });
});

function recoverAfterStreamFailure(fixture: ReturnType<typeof buildStore>, runId: string | null, partialContent: string) {
  return _recoverAfterStreamFailure(fixture.get, {
    apiSessionId: 'server-A', localSessionId: 'local-A', assistantMessageId: 'assistant-A', processingKey: 'server-A',
    runId, clientMessageId: 'turn-A', partialContent, set: fixture.set, currentSession: fixture.a,
  });
}

describe('automatic resume from a checkpoint', () => {
  it('replaces the note instead of appending to it', async () => {
    vi.useFakeTimers();
    const fixture = buildStore();
    const recovering = recoverAfterStreamFailure(fixture, 'run-A', '');
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    expect(fixture.store.resumeChatRun).toHaveBeenCalledWith('run-A', 'server-A');
    const content = assistant(fixture.store).content as string;
    expect(content).toBe(translate('chat.recovery.resumedNote'));
    expect(content).toContain('已从断点自动续跑');
    expect(assistant(fixture.store).metadata).toMatchObject({ status: 'interrupted', recovering: false, auto_resumed_from: 'run-A' });
  });

  it('keeps real partial text above the new line', async () => {
    vi.useFakeTimers();
    const fixture = buildStore();
    const recovering = recoverAfterStreamFailure(fixture, 'run-A', REAL_ANSWER);
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    const content = assistant(fixture.store).content as string;
    expect(content.startsWith(`${REAL_ANSWER}\n\n`)).toBe(true);
    expect(content.endsWith(translate('chat.recovery.resumedNote'))).toBe(true);
  });

  it('shows the notes in English when the interface is English, and still replaces them', async () => {
    useLanguageStore.getState().setLanguage('en');
    vi.useFakeTimers();
    const fixture = buildStore();
    const recovering = recoverAfterStreamFailure(fixture, 'run-A', '');
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    const content = assistant(fixture.store).content as string;
    expect(content).toBe('(Connection lost. Resumed from the checkpoint automatically. The latest results are in the new turn below.)');
    expect(content).not.toMatch(/[\u4e00-\u9fff]/);
  });

  it('does not treat real text that starts like a note as a note', async () => {
    vi.useFakeTimers();
    const fixture = buildStore();
    const lookalike = '服务端返回的结果显示，宿主范围与受体结合蛋白之间存在明显的相关性，详见下表所列数据。';
    const recovering = recoverAfterStreamFailure(fixture, 'run-A', lookalike);
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    expect((assistant(fixture.store).content as string).startsWith(`${lookalike}\n\n`)).toBe(true);
  });
});
