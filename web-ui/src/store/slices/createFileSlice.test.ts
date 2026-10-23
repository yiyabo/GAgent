import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';
import type { FileListResponse } from '@api/upload';
import type { ChatSession, UploadedFile } from '@/types';

vi.mock('@api/upload', () => ({ uploadApi: { listFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn() } }));
vi.mock('@api/chat', () => ({ chatApi: {} }));
vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));

import { uploadApi } from '@api/upload';
import { createFileSlice } from './createFileSlice';
import { createSessionSlice } from './createSessionSlice';

function deferred<T>() {
  let resolve: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve: resolve! };
}

function session(id: string): ChatSession {
  return { id: `local-${id}`, session_id: `server-${id}`, title: id, messages: [], created_at: new Date(), updated_at: new Date() };
}

const serverFile = (id: string): UploadedFile => ({
  file_id: id, file_path: `/runtime/uploads/${id}_${id}.fasta`, file_name: `${id}_${id}.fasta`, original_name: `${id}.fasta`,
  file_size: '10 B', file_type: 'application/octet-stream', uploaded_at: new Date().toISOString(), source: 'server',
});

const projectRef = (id: string): UploadedFile => ({
  file_id: `project_${id}`, file_path: `project/${id}.txt`, file_name: `${id}.txt`, original_name: `${id}.txt`, file_size: 'Project File', file_type: 'project_reference', uploaded_at: new Date().toISOString(), category: 'project',
});

function listing(...ids: string[]): FileListResponse {
  return {
    success: true, session_id: 'server-A', total: ids.length,
    files: ids.map((id) => ({ file_id: id, file_path: `/runtime/uploads/${id}_${id}.fasta`, file_name: `${id}_${id}.fasta`, original_name: `${id}.fasta`, file_size: '10 B', uploaded_at: new Date().toISOString() })),
  };
}

function httpError(status: number): AxiosError {
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', undefined, undefined,
    { status, data: { detail: 'x' }, statusText: '', headers: {}, config: {} as never });
}

function buildStore() {
  const a = session('A');
  const b = session('B');
  const store: any = {};
  const get = () => store;
  const set = (updater: any) => Object.assign(store, typeof updater === 'function' ? updater(store) : updater);
  Object.assign(store, createSessionSlice(set as any, get, {} as any), createFileSlice(set as any, get, {} as any));
  store.sessions = [a, b];
  store.currentSession = a;
  store.messages = [];
  return { store, a, b };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(uploadApi.listFiles).mockResolvedValue(listing());
});
afterEach(() => { vi.restoreAllMocks(); });

describe('composer attachments', () => {
  it('each chat keeps its own unsent attachments across switches', () => {
    const { store, a, b } = buildStore();
    store.setUploadedFiles([projectRef('A-notes')]);
    store.setCurrentSession(b);
    expect(store.uploadedFiles).toEqual([]);
    store.setUploadedFiles([projectRef('B-notes')]);
    store.setCurrentSession(a);
    expect(store.uploadedFiles.map((f: UploadedFile) => f.file_id)).toEqual(['project_A-notes']);
    store.setCurrentSession(null);
    expect(store.uploadedFiles).toEqual([]);
    expect(store.attachmentDrafts['local-B'].map((f: UploadedFile) => f.file_id)).toEqual(['project_B-notes']);
  });

  it('removing an unsent chip deletes its server file; a project reference is only unlinked', async () => {
    const { store } = buildStore();
    store.setUploadedFiles([serverFile('f1'), projectRef('p'), serverFile('f2')]);
    vi.mocked(uploadApi.deleteFile).mockResolvedValue({ success: true, message: 'ok' });
    await store.removeUploadedFile('f1');
    expect(uploadApi.deleteFile).toHaveBeenCalledWith('f1', 'server-A');
    await store.removeUploadedFile('project_p');
    expect(uploadApi.deleteFile).toHaveBeenCalledTimes(1);
    expect(store.uploadedFiles.map((f: UploadedFile) => f.file_id)).toEqual(['f2']);
  });

  it('a failed removal keeps the chip and reports the error; a file already gone is just dropped', async () => {
    const { store } = buildStore();
    store.setUploadedFiles([serverFile('f1'), serverFile('f2')]);
    vi.mocked(uploadApi.deleteFile).mockRejectedValueOnce(httpError(500));
    await expect(store.removeUploadedFile('f1')).rejects.toThrow('500');
    expect(store.uploadedFiles.map((f: UploadedFile) => f.file_id)).toEqual(['f1', 'f2']);
    vi.mocked(uploadApi.deleteFile).mockRejectedValueOnce(httpError(404));
    await store.removeUploadedFile('f1');
    expect(store.uploadedFiles.map((f: UploadedFile) => f.file_id)).toEqual(['f2']);
  });

  it('a file that is not a pending chip is never deleted from the server', async () => {
    const { store } = buildStore();
    await store.removeUploadedFile('sent-earlier');
    expect(uploadApi.deleteFile).not.toHaveBeenCalled();
  });

  it('the server check drops chips whose file is gone but keeps one uploaded during the check', async () => {
    const { store } = buildStore();
    store.setUploadedFiles([projectRef('p'), serverFile('kept'), serverFile('deleted')]);
    const list = deferred<FileListResponse>();
    vi.mocked(uploadApi.listFiles).mockReturnValueOnce(list.promise);
    const checking = store.syncUploadedFilesFromServer();
    store.setUploadedFiles([...store.uploadedFiles, serverFile('new')]);
    list.resolve(listing('kept'));
    await checking;
    expect(uploadApi.listFiles).toHaveBeenCalledWith('server-A');
    expect(store.uploadedFiles.map((f: UploadedFile) => f.file_id)).toEqual(['project_p', 'kept', 'new']);
  });

  it('the server check does not list files when only project references are pending, and survives a failure', async () => {
    const { store } = buildStore();
    store.setUploadedFiles([projectRef('p')]);
    await store.syncUploadedFilesFromServer();
    expect(uploadApi.listFiles).not.toHaveBeenCalled();
    store.setUploadedFiles([serverFile('f1')]);
    vi.mocked(uploadApi.listFiles).mockRejectedValueOnce(new Error('offline'));
    await store.syncUploadedFilesFromServer();
    expect(store.uploadedFiles.map((f: UploadedFile) => f.file_id)).toEqual(['f1']);
  });

  it('taking the attachments for a message empties only the current chat', () => {
    const { store, b } = buildStore();
    store.setUploadedFiles([serverFile('f1')]);
    store.attachmentDrafts = { ...store.attachmentDrafts, [b.id]: [serverFile('other')] };
    expect(store.takeComposerAttachments().map((f: UploadedFile) => f.file_id)).toEqual(['f1']);
    expect(store.uploadedFiles).toEqual([]);
    expect(store.takeComposerAttachments()).toEqual([]);
    expect(store.attachmentDrafts[b.id].map((f: UploadedFile) => f.file_id)).toEqual(['other']);
  });
});
