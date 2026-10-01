import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatSession } from '@/types';

vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@api/chat', () => ({ chatApi: {
  getHistory: vi.fn(), getActiveRun: vi.fn(), updateSession: vi.fn(), getActionStatus: vi.fn(), autotitleSession: vi.fn(),
} }));

import { chatApi } from '@api/chat';
import { SessionStorage } from '@/utils/sessionStorage';
import { createSessionSlice } from '../createSessionSlice';
import { createUISlice } from '../createUISlice';
import { createMessageSlice } from './index';
import { createSessionAccess } from './sessionState';
import { flushAnalysisText, startActionStatusPolling } from './helpers';
import { recoverAfterStreamFailure } from './recovery';
import { handleFinal, handleJobUpdate, handleProgressStatus, handleThinkingStep, processBackgroundDispatch, processFinalPayload } from './streamHandlers';
import type { StreamHandlerContext } from './types';

const session = (id: string, planId: number | null = null): ChatSession => ({
  id: `local-${id}`, session_id: `server-${id}`, title: id,
  created_at: new Date(), updated_at: new Date(), isUserNamed: true,
  plan_id: planId, plan_title: planId == null ? null : `Plan ${planId}`,
  messages: [
    { id: `user-${id}`, type: 'user', content: `Request ${id}`, timestamp: new Date(), metadata: { client_message_id: `turn-${id}` } },
    { id: `assistant-${id}`, type: 'assistant', content: '', timestamp: new Date(), metadata: { status: 'pending', chat_run_id: `run-${id}`, client_message_id: `turn-${id}` } },
  ],
});

function buildStore() {
  const a = session('A');
  const b = session('B', 9);
  const store: any = {
    sessions: [a, b], currentSession: null,
    syncUploadedFilesFromServer: vi.fn().mockResolvedValue(undefined), clearUploadedFiles: vi.fn(),
    uploadedFiles: [], memoryEnabled: false, relevantMemories: [],
  };
  const get = () => store;
  const set = (updater: any) => Object.assign(store, typeof updater === 'function' ? updater(store) : updater);
  Object.assign(store, createSessionSlice(set as any, get, {} as any), createMessageSlice(set as any, get, {} as any), createUISlice(set as any, get, {} as any));
  store.sessions = [a, b];
  store.setCurrentSession(a);
  store.setActiveRunId('server-A', 'run-A');
  store.setSessionProcessing('server-A', true);
  store.setActiveRunId('server-B', 'run-B');
  store.setSessionProcessing('server-B', true);
  return { store, get, set, a, b };
}

function streamContext(fixture: ReturnType<typeof buildStore>): StreamHandlerContext {
  const { get, set, a } = fixture;
  const source = createSessionAccess(get, set, a.id);
  const state = {
    streamedContent: '', lastFlushedContent: '', flushHandle: null,
    thinkingDeltaFlushHandle: null, pendingThinkingDeltas: {}, pendingThinkingDeltaStartedAt: {},
    finalPayload: null, jobFinalized: false, isBackgroundDispatch: false,
  };
  return {
    get, set, currentSession: a, assistantMessageId: 'assistant-A', mergedMetadata: {}, state,
    startActionStatusPolling: vi.fn(), scheduleFlush: vi.fn(),
    flushAnalysisText: (force) => flushAnalysisText(source.get, 'assistant-A', state, force),
  };
}

const sourceAssistant = (store: any) => store.sessions.find((entry: ChatSession) => entry.id === 'local-A').messages.find((message: any) => message.id === 'assistant-A');

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(chatApi.getHistory).mockResolvedValue({ data: { success: true, messages: [], has_more: false } } as any);
  vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [] } } as any);
  vi.mocked(chatApi.updateSession).mockResolvedValue({} as any);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('source session isolation', () => {
  it('retains A completion, context and event ownership while B is active, then restores A from its latest entity', async () => {
    const fixture = buildStore();
    const { store, a, b } = fixture;
    const ctx = streamContext(fixture);
    store.setCurrentSession(b);
    const dispatch = vi.spyOn(window, 'dispatchEvent');
    const activeMessages = store.messages;

    handleProgressStatus(ctx, { tool: 'web_search', label: 'Searching', status: 'running' });
    handleThinkingStep(ctx, { step: { iteration: 1, thought: 'Checking evidence', status: 'thinking' } });
    ctx.state.finalPayload = {
      response: 'A finished', actions: [],
      metadata: { status: 'completed', plan_id: 77, plan_title: 'Plan A', task_id: 7, workflow_id: 'workflow-A', session_id: 'server-A', agent_workflow: true },
    };
    await processFinalPayload(ctx);
    await Promise.resolve();

    expect(store.currentSession).toBe(b);
    expect(store.messages).toBe(activeMessages);
    expect(store.currentPlanId).toBe(9);
    expect(store.activeRunIds.get('server-B')).toBe('run-B');
    expect(store.processingSessionIds.has('server-B')).toBe(true);
    expect(store.processingSessionIds.has('server-A')).toBe(false);
    expect(sourceAssistant(store)).toMatchObject({ content: 'A finished', metadata: { status: 'completed', chat_run_id: 'run-A', client_message_id: 'turn-A' } });
    expect(store.sessions[0]).toMatchObject({ plan_id: 77, current_task_id: 7, workflow_id: 'workflow-A' });
    expect(chatApi.updateSession).toHaveBeenCalledWith('server-A', expect.objectContaining({ plan_id: 77, current_task_id: 7 }));
    expect(chatApi.updateSession).not.toHaveBeenCalledWith('server-B', expect.anything());
    expect(dispatch.mock.calls.map(([event]) => (event as CustomEvent).detail).every((detail) => detail.session_id === 'server-A')).toBe(true);
    expect(SessionStorage.getCurrentSessionId()).toBe('server-B');

    store.setCurrentSession(a); // Deliberately select the stale pre-completion snapshot.
    expect(store.currentSession.plan_id).toBe(77);
    expect(store.messages.find((message: any) => message.id === 'assistant-A').content).toBe('A finished');
  });

  it('keeps background dispatch and terminal job updates associated with A after switching to B', async () => {
    const fixture = buildStore();
    const { store, b } = fixture;
    const ctx = streamContext(fixture);
    store.setCurrentSession(b);
    expect(handleFinal(ctx, { payload: { response: 'Submitted', actions: [], metadata: { background_category: 'code_executor', plan_id: 55, plan_title: 'Background A' } } })).toBe(true);
    processBackgroundDispatch(ctx);
    expect(store.currentSession).toBe(b);
    expect(sourceAssistant(store).metadata).toMatchObject({ plan_id: 55, chat_run_id: 'run-A', status: 'running' });
    expect(chatApi.updateSession).toHaveBeenCalledWith('server-A', expect.objectContaining({ plan_id: 55 }));

    await handleJobUpdate(ctx, { payload: { status: 'succeeded', result: { bound_plan_id: 55, plan_title: 'Background A', final_summary: 'Job A done', steps: [] } } });
    expect(sourceAssistant(store).metadata.status).toBe('completed');
    expect(store.currentSession).toBe(b);
    expect(store.currentPlanId).toBe(9);
    expect(chatApi.updateSession).not.toHaveBeenCalledWith('server-B', expect.anything());
  });

  it('polls an action result into its source entity and reloads source history without affecting B', async () => {
    const fixture = buildStore();
    const { store, get, set, b } = fixture;
    const source = createSessionAccess(get, set, 'local-A');
    const history = vi.spyOn(store, 'loadChatHistory');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'completed', result: { reply: 'Action A done' } }) }));
    store.setCurrentSession(b);
    startActionStatusPolling(source.get, 'action-A', 'assistant-A', 'completed');
    await vi.waitFor(() => expect(history).toHaveBeenCalledWith('server-A'));
    expect(sourceAssistant(store).content).toBe('Action A done');
    expect(store.currentSession).toBe(b);
    expect(store.messages).toBe(b.messages);
  });

  it('does not load a late A history response into B or clear B loading state', async () => {
    const { store, b } = buildStore();
    let resolveHistory: (value: any) => void;
    vi.mocked(chatApi.getHistory).mockImplementationOnce(() => new Promise((resolve) => { resolveHistory = resolve; }) as any);
    const loadingA = store.loadChatHistory('server-A');
    store.setCurrentSession(b);
    store.historyLoading = true;
    resolveHistory!({ data: { success: true, messages: [{ id: 5, role: 'assistant', content: 'Historical A', metadata: { deep_think_job_id: 'run-old' } }], has_more: false } });
    await loadingA;
    expect(store.currentSession).toBe(b);
    expect(store.messages).toBe(b.messages);
    expect(store.historyLoading).toBe(true);
    expect(store.sessions[0].messages.some((message: any) => message.content === 'Historical A')).toBe(true);
    expect(sourceAssistant(store).metadata.chat_run_id).toBe('run-A');
  });

  it('does not attach a late active-run lookup after navigation to B', async () => {
    const { store, b } = buildStore();
    let resolveRun: (value: any) => void;
    vi.mocked(chatApi.getActiveRun).mockImplementationOnce(() => new Promise((resolve) => { resolveRun = resolve; }) as any);
    const resume = store.resumeActiveChatRunIfAny('server-A');
    store.setCurrentSession(b);
    resolveRun!({ data: { runs: [{ run_id: 'run-A', status: 'running' }] } });
    await resume;
    expect(store.currentSession).toBe(b);
    expect(store.messages).toBe(b.messages);
    expect(store.activeRunIds.get('server-B')).toBe('run-B');
  });

  it('does not replace a known run with an unrelated active session run during history resume', async () => {
    const { store } = buildStore();
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [{ run_id: 'run-unrelated', status: 'running' }] } } as any);
    await store.resumeActiveChatRunIfAny('server-A');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(store.messages).toHaveLength(2);
    expect(sourceAssistant(store).metadata.chat_run_id).toBe('run-A');
  });

  it('preserves an active task selection when a background response only changes the plan', () => {
    const fixture = buildStore();
    fixture.store.currentTaskId = 7;
    fixture.store.currentTaskName = 'Task A';
    fixture.store.currentWorkflowId = 'workflow-A';
    handleFinal(streamContext(fixture), { payload: { response: 'Submitted', metadata: { background_category: 'code_executor', plan_id: 55 } } });
    expect(fixture.store.currentTaskId).toBe(7);
    expect(fixture.store.currentTaskName).toBe('Task A');
    expect(fixture.store.currentWorkflowId).toBe('workflow-A');
  });

  it('keeps a newly sent run bound to A when its SSE final arrives after switching to B', async () => {
    const { store, b } = buildStore();
    store.setSessionProcessing('server-A', false);
    store.clearMessages();
    let deliver: (value: any) => void;
    const reading = new Promise((resolve) => { deliver = resolve; });
    const reader = { read: () => reading, cancel: vi.fn().mockResolvedValue(undefined) };
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ run_id: 'run-new', session_id: 'server-A' }) })
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => reader } });
    vi.stubGlobal('fetch', fetchMock);
    const sending = store.sendMessage('New request A');
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    store.setCurrentSession(b);
    const uploadSyncCount = store.syncUploadedFilesFromServer.mock.calls.length;
    deliver!({ done: false, value: new TextEncoder().encode('id: 0\ndata: {"type":"final","payload":{"response":"New A answer","actions":[],"metadata":{"status":"completed","plan_id":88}}}\n\n') });
    await sending;
    expect(store.currentSession).toBe(b);
    expect(store.currentPlanId).toBe(9);
    const completed = store.sessions[0].messages.find((message: any) => message.type === 'assistant');
    expect(completed).toMatchObject({ content: 'New A answer', metadata: { chat_run_id: 'run-new', status: 'completed' } });
    expect(completed.metadata.client_message_id).toBe(store.sessions[0].messages[0].metadata.client_message_id);
    expect(store.syncUploadedFilesFromServer).toHaveBeenCalledTimes(uploadSyncCount);
  });

  it('replays a failed stream from empty accumulators and cancels old thinking callbacks after switching to B', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const { store, b } = buildStore();
    store.setSessionProcessing('server-A', false);
    store.clearMessages();
    let resolveActive: (value: any) => void;
    vi.mocked(chatApi.getActiveRun).mockImplementationOnce(() => new Promise((resolve) => { resolveActive = resolve; }) as any);
    const sse = (events: any[]) => ({ ok: true, body: { getReader: () => ({
      read: vi.fn().mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(events.map((event, index) => `id: ${index}\ndata: ${JSON.stringify(event)}\n\n`).join('')) }),
      cancel: vi.fn().mockResolvedValue(undefined),
    }) } });
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ run_id: 'run-new' }) })
      .mockResolvedValueOnce(sse([
        { type: 'delta', content: 'Hello' }, { type: 'thinking_delta', iteration: 1, delta: 'Original thought.' }, { type: 'error', message: 'Disconnected' },
      ]))
      .mockResolvedValueOnce(sse([
        { type: 'delta', content: 'Hello' }, { type: 'thinking_delta', iteration: 1, delta: 'Original thought.' },
        { type: 'final', payload: { response: 'Hello', actions: [], metadata: { status: 'completed' } } },
      ])));
    const sending = store.sendMessage('New request A');
    await vi.waitFor(() => expect(chatApi.getActiveRun).toHaveBeenCalled());
    store.setCurrentSession(b);
    resolveActive!({ data: { runs: [{ run_id: 'run-new', status: 'running' }] } });
    await vi.runAllTimersAsync();
    await sending;
    const completed = store.sessions[0].messages.find((message: any) => message.type === 'assistant');
    expect(completed.content).toBe('Hello');
    expect(completed.thinking_process.steps[0].thought).toBe('Original thought.');
    expect(completed.metadata.recovering).toBe(false);
    expect(store.currentSession).toBe(b);
    expect(store.processingSessionIds.has('server-B')).toBe(true);
  });

  it('ignores superseded history requests for the same session and preserves the newest cursor', async () => {
    const { store } = buildStore();
    let resolveOld: (value: any) => void;
    let resolveNew: (value: any) => void;
    vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [{ run_id: 'run-A', status: 'running' }] } } as any);
    vi.mocked(chatApi.getHistory).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }) as any)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve; }) as any);
    const old = store.loadChatHistory('server-A');
    const latest = store.loadChatHistory('server-A');
    resolveNew!({ data: { success: true, messages: [{ id: 6, role: 'assistant', content: 'Newest history' }], has_more: true, next_before_id: 6 } });
    await latest;
    resolveOld!({ data: { success: true, messages: [{ id: 5, role: 'assistant', content: 'Stale history' }], has_more: false, next_before_id: 5 } });
    await old;
    expect(store.messages.map((message: any) => message.content)).toContain('Newest history');
    expect(store.messages.map((message: any) => message.content)).not.toContain('Stale history');
    expect(store.historyBeforeId).toBe(6);
    expect(store.historyHasMore).toBe(true);
    expect(store.historyLoading).toBe(false);
  });
});

describe('correlated stream recovery', () => {
  const recoveryOptions = (fixture: ReturnType<typeof buildStore>) => ({
    get: fixture.get, set: fixture.set, apiSessionId: 'server-A', localSessionId: 'local-A',
    assistantMessageId: 'assistant-A', processingKey: 'server-A', runId: 'run-A', clientMessageId: 'turn-A', resumeRun: vi.fn().mockResolvedValue(undefined),
  });

  it('rejects a previous-turn answer and never resumes an unrelated active run', async () => {
    vi.useFakeTimers();
    const fixture = buildStore();
    const options = recoveryOptions(fixture);
    const history = vi.spyOn(fixture.store, 'loadChatHistory');
    vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [{ run_id: 'run-other', status: 'running' }] } } as any);
    vi.mocked(chatApi.getHistory).mockResolvedValue({ data: { messages: [{ role: 'assistant', content: 'Old answer '.repeat(10), metadata: { deep_think_job_id: 'run-old' } }] } } as any);
    const recovering = recoverAfterStreamFailure(options);
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(false);
    expect(options.resumeRun).not.toHaveBeenCalled();
    expect(history).not.toHaveBeenCalled();
  });

  it('accepts a matching run answer, including a short answer, after switching to B mid-recovery', async () => {
    vi.useFakeTimers();
    const fixture = buildStore();
    const options = recoveryOptions(fixture);
    let resolveHistory: (value: any) => void;
    const response = { data: { success: true, messages: [
      { id: 10, role: 'user', content: 'Request A', metadata: { client_message_id: 'turn-A' } },
      { id: 11, role: 'assistant', content: 'OK', metadata: { deep_think_job_id: 'run-A', status: 'completed' } },
    ], has_more: false } };
    vi.mocked(chatApi.getHistory).mockImplementationOnce(() => new Promise((resolve) => { resolveHistory = resolve; }) as any).mockResolvedValue(response as any);
    const recovering = recoverAfterStreamFailure(options);
    await vi.advanceTimersByTimeAsync(800);
    fixture.store.setCurrentSession(fixture.b);
    resolveHistory!(response);
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    expect(fixture.store.currentSession).toBe(fixture.b);
    expect(fixture.store.messages).toBe(fixture.b.messages);
    expect(fixture.store.processingSessionIds.has('server-A')).toBe(false);
    expect(fixture.store.processingSessionIds.has('server-B')).toBe(true);
    expect(fixture.store.sessions[0].messages.map((message: any) => message.content)).toEqual(['Request A', 'OK']);
  });

  it('recovers by the current client message interval when assistant run metadata is absent', async () => {
    vi.useFakeTimers();
    const fixture = buildStore();
    const options = { ...recoveryOptions(fixture), runId: null };
    vi.mocked(chatApi.getHistory).mockResolvedValue({ data: { success: true, messages: [
      { id: 1, role: 'assistant', content: 'Previous response', metadata: {} },
      { id: 2, role: 'user', content: 'Request A', metadata: { client_message_id: 'turn-A' } },
      { id: 3, role: 'assistant', content: 'Current response', metadata: { status: 'completed' } },
    ], has_more: false } } as any);
    const recovering = recoverAfterStreamFailure(options);
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    expect(options.resumeRun).not.toHaveBeenCalled();
    expect(fixture.store.messages.map((message: any) => message.content)).toEqual(['Previous response', 'Request A', 'Current response']);
  });

  it('reconnects only the expected active run and keeps recovery updates in A while B is active', async () => {
    const fixture = buildStore();
    const options = recoveryOptions(fixture);
    fixture.store.setCurrentSession(fixture.b);
    vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [{ run_id: 'run-other', status: 'running' }, { run_id: 'run-A', status: 'queued' }] } } as any);
    expect(await recoverAfterStreamFailure(options)).toBe(true);
    expect(options.resumeRun).toHaveBeenCalledWith('run-A');
    expect(sourceAssistant(fixture.store).metadata.recovering).toBe(true);
    expect(fixture.store.currentSession).toBe(fixture.b);
  });

  it('retains the correlated reply if the follow-up history synchronization fails', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const fixture = buildStore();
    fixture.store.setCurrentSession(fixture.b);
    fixture.store.loadChatHistory = vi.fn().mockRejectedValue(new Error('History unavailable'));
    vi.mocked(chatApi.getHistory).mockResolvedValue({ data: { messages: [
      { id: 11, role: 'assistant', content: 'OK', metadata: { deep_think_job_id: 'run-A', status: 'completed' } },
    ] } } as any);
    const recovering = recoverAfterStreamFailure(recoveryOptions(fixture));
    await vi.runAllTimersAsync();
    expect(await recovering).toBe(true);
    expect(sourceAssistant(fixture.store)).toMatchObject({ content: 'OK', metadata: { status: 'completed', analysis_text: 'OK', recovering: false } });
    expect(fixture.store.currentSession).toBe(fixture.b);
    expect(fixture.store.processingSessionIds.has('server-A')).toBe(false);
  });
});
