import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatSession } from '@/types';

vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@api/chat', () => ({ chatApi: {
  getHistory: vi.fn(), getActiveRun: vi.fn(), updateSession: vi.fn(), getActionStatus: vi.fn(), autotitleSession: vi.fn(),
  getResumeInfo: vi.fn(),
} }));

import { chatApi } from '@api/chat';
import { resetAutoResumeGuardsForTest } from './recovery';
import { createSessionSlice } from '../createSessionSlice';
import { createUISlice } from '../createUISlice';
import { _isRecoveryNote, _looksLikeSubstantialAssistant, _recoverAfterStreamFailure, createMessageSlice } from './index';

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
    expect(_looksLikeSubstantialAssistant(`对话已中断（服务端已重启或无进行中的任务）。请重新发送消息。${'。'.repeat(40)}`)).toBe(false);
    expect(_looksLikeSubstantialAssistant(`连接中断，正在从服务端同步本轮已完成的结果…${'…'.repeat(40)}`)).toBe(false);
  });
});

describe('the interruption note shown while a turn is recovering', () => {
  it('is recognised as a note, and ordinary text is not', () => {
    expect(_isRecoveryNote('连接中断，正在自动重连并继续接收结果…')).toBe(true);
    expect(_isRecoveryNote('服务端已恢复，正在从断点自动续跑…')).toBe(true);
    expect(_isRecoveryNote(REAL_ANSWER)).toBe(false);
    expect(_isRecoveryNote('')).toBe(false);
  });

  it('is what a turn without any streamed text shows first, and is not kept as a partial reply', () => {
    const fixture = buildStore();
    void recoverAfterStreamFailure(fixture, null, '');
    const shown = assistant(fixture.store).content as string;
    expect(shown.length).toBeGreaterThan(0);
    expect(_isRecoveryNote(shown)).toBe(true);
    expect(_looksLikeSubstantialAssistant(shown)).toBe(false);
    expect(assistant(fixture.store).metadata).toMatchObject({ status: 'pending', recovering: true });
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
    expect(content).toContain('已从断点自动续跑');
    expect(content).not.toContain('服务端已恢复');
    expect(content.includes('\n\n')).toBe(false);
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
    expect(content).toContain('已从断点自动续跑');
  });
});
