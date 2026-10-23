import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@api/chat', () => ({ chatApi: {
  getHistory: vi.fn(), getActiveRun: vi.fn(), updateSession: vi.fn(), autotitleSession: vi.fn(),
} }));
vi.mock('@api/upload', () => ({ uploadApi: { uploadFile: vi.fn(), deleteFile: vi.fn(), listFiles: vi.fn() } }));
vi.mock('../../chatUtils', async (load) => {
  const actual = await load<typeof import('../../chatUtils')>();
  return { ...actual, postChatRun: vi.fn(), streamRunEvents: vi.fn() };
});
import { chatApi } from '@api/chat';
import { uploadApi } from '@api/upload';
import { postChatRun, streamRunEvents } from '../../chatUtils';
import { createMessageSlice } from './index';
import { createSessionSlice } from '../createSessionSlice';
import { createUISlice } from '../createUISlice';
import { createFileSlice } from '../createFileSlice';

function storeFixture() {
  const a = { id: 'local-A', session_id: 'server-A', title: 'A', created_at: new Date(), updated_at: new Date(), messages: [] };
  const b = { ...a, id: 'local-B', session_id: 'server-B', title: 'B', messages: [] };
  const store: any = { sessions: [a, b], currentSession: null, memoryEnabled: true, relevantMemories: [] };
  const get = () => store;
  const set = (patch: any) => Object.assign(store, typeof patch === 'function' ? patch(store) : patch);
  Object.assign(
    store,
    createSessionSlice(set as any, get, {} as any),
    createMessageSlice(set as any, get, {} as any),
    createUISlice(set as any, get, {} as any),
    createFileSlice(set as any, get, {} as any),
  );
  store.sessions = [a, b];
  store.setCurrentSession(a);
  return { store, a, b };
}

const uploadResponse = (id: string, name: string) => ({
  success: true, file_id: id, file_path: `/runtime/session_server-A/uploads/${id}_${name}`, file_name: `${id}_${name}`,
  original_name: name, file_size: '48 KB', file_type: 'application/octet-stream', uploaded_at: '2026-10-23T12:00:00',
});
const file = (name: string) => new File(['>seq\nACGT\n'], name);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(chatApi.getHistory).mockResolvedValue({ data: { success: true, messages: [], has_more: false } } as any);
  vi.mocked(chatApi.getActiveRun).mockResolvedValue({ data: { runs: [] } } as any);
  vi.mocked(chatApi.updateSession).mockResolvedValue({} as any);
  vi.mocked(chatApi.autotitleSession).mockResolvedValue({} as any);
  vi.mocked(uploadApi.listFiles).mockResolvedValue({ success: true, files: [], total: 0, session_id: 'server-A' });
  vi.mocked(postChatRun).mockResolvedValue({ run_id: 'run-1' } as any);
  vi.mocked(streamRunEvents).mockImplementation(async function* () {
    yield { seq: 1, event: { type: 'final', payload: { response: 'Done', actions: [], metadata: { status: 'completed', session_id: 'server-A', unified_stream: true } } } as any };
  });
});

describe('attachments travel with the message they were added to', () => {
  it('keeps every uploaded file, sends them all with the next message and empties the composer', async () => {
    const { store } = storeFixture();
    vi.mocked(uploadApi.uploadFile)
      .mockResolvedValueOnce(uploadResponse('a1', 'lambda.fasta') as any)
      .mockResolvedValueOnce(uploadResponse('b2', 'T7.fasta') as any);
    await Promise.all([store.uploadFile(file('lambda.fasta')), store.uploadFile(file('T7.fasta'))]);
    expect(store.uploadedFiles.map((f: any) => f.original_name)).toEqual(['lambda.fasta', 'T7.fasta']);
    expect(uploadApi.uploadFile).toHaveBeenCalledWith(expect.any(File), 'server-A');

    await store.sendMessage('Compare these two phages');

    const request = vi.mocked(postChatRun).mock.calls[0][0] as any;
    expect(request.context.attachments.map((a: any) => a.name)).toEqual(['lambda.fasta', 'T7.fasta']);
    expect(request.context.attachments[0]).toEqual({ type: 'file', name: 'lambda.fasta', path: '/runtime/session_server-A/uploads/a1_lambda.fasta' });
    const user = store.messages.find((m: any) => m.type === 'user');
    expect(user.metadata.attachments).toHaveLength(2);
    expect(store.uploadedFiles).toEqual([]);
    expect(store.attachmentDrafts['local-A']).toEqual([]);
    expect(uploadApi.deleteFile).not.toHaveBeenCalled();
  });

  it('a follow-up turn sends no new files but tells the server where the earlier files are', async () => {
    const { store } = storeFixture();
    vi.mocked(uploadApi.uploadFile).mockResolvedValueOnce(uploadResponse('a1', 'lambda.fasta') as any);
    await store.uploadFile(file('lambda.fasta'));
    await store.sendMessage('Annotate this genome');
    vi.mocked(postChatRun).mockResolvedValueOnce({ run_id: 'run-2' } as any);
    await store.sendMessage('Now run CheckV on it');

    const second = vi.mocked(postChatRun).mock.calls[1][0] as any;
    expect(second.context.attachments).toBeUndefined();
    const earlier = second.history.find((h: any) => h.role === 'user' && h.content === 'Annotate this genome');
    expect(earlier.metadata.attachments.map((a: any) => a.name)).toEqual(['lambda.fasta']);
    const current = second.history.find((h: any) => h.content === 'Now run CheckV on it');
    expect(current.metadata).toBeUndefined();
  });

  it('a retried message brings its own attachments and leaves the composer alone', async () => {
    const { store } = storeFixture();
    vi.mocked(uploadApi.uploadFile).mockResolvedValueOnce(uploadResponse('c3', 'next.fasta') as any);
    await store.uploadFile(file('next.fasta'));
    const original = [{ type: 'file', name: 'lambda.fasta', path: '/runtime/x/a1_lambda.fasta' }];
    await store.sendMessage('Annotate this genome', { attachments: original });

    expect((vi.mocked(postChatRun).mock.calls[0][0] as any).context.attachments).toEqual(original);
    expect(store.uploadedFiles.map((f: any) => f.original_name)).toEqual(['next.fasta']);
  });

  it('an upload that finishes after switching chats lands in the chat it started in', async () => {
    const { store, a, b } = storeFixture();
    let finish!: (value: any) => void;
    vi.mocked(uploadApi.uploadFile).mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const pending = store.uploadFile(file('lambda.fasta'));
    expect(store.uploadingFiles).toEqual([expect.objectContaining({ name: 'lambda.fasta', sessionKey: 'local-A' })]);
    store.setCurrentSession(b);
    finish(uploadResponse('a1', 'lambda.fasta'));
    await pending;

    expect(store.uploadingFiles).toEqual([]);
    expect(store.uploadedFiles).toEqual([]);
    expect(store.attachmentDrafts['local-A'].map((f: any) => f.original_name)).toEqual(['lambda.fasta']);
    store.setCurrentSession(a);
    expect(store.uploadedFiles.map((f: any) => f.original_name)).toEqual(['lambda.fasta']);
  });
});
