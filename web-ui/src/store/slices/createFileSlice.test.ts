import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FileListResponse } from '@api/upload';
import type { ChatSession, UploadedFile } from '@/types';

vi.mock('@api/upload', () => ({ uploadApi: { listFiles: vi.fn() } }));
vi.mock('@api/chat', () => ({ chatApi: {} }));
vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));

import { uploadApi } from '@api/upload';
import { createFileSlice } from './createFileSlice';
import { createSessionSlice } from './createSessionSlice';

function deferred<T>() {
  let resolve: (value: T) => void;
  let reject: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve: resolve!, reject: reject! };
}

function session(id: string): ChatSession {
  return { id: `local-${id}`, session_id: `server-${id}`, title: id, messages: [], created_at: new Date(), updated_at: new Date() };
}

function fileList(id: string, fileId = id): FileListResponse {
  return {
    success: true, session_id: `server-${id}`, total: 1,
    files: [{ file_id: fileId, file_path: `session_${id}/uploads/${fileId}.txt`, file_name: `${fileId}.txt`, original_name: `${fileId}.txt`, file_size: '10 B', uploaded_at: new Date().toISOString() }],
  };
}

const projectRef = (id: string): UploadedFile => ({
  file_id: `project_${id}`, file_path: `project/${id}.txt`, file_name: `${id}.txt`, original_name: `${id}.txt`, file_size: 'Project File', file_type: 'project_reference', uploaded_at: new Date().toISOString(), category: 'project',
});

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

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('session upload synchronization', () => {
  it('ignores a late A list success after session selection synchronizes B attachments', async () => {
    const { store, b } = buildStore();
    const responseA = deferred<FileListResponse>();
    vi.mocked(uploadApi.listFiles).mockReturnValueOnce(responseA.promise).mockResolvedValueOnce(fileList('B'));
    const syncingA = store.syncUploadedFilesFromServer();
    store.setCurrentSession(b); // Exercise the actual session-selection sync hook.
    await vi.waitFor(() => expect(store.uploadedFiles[0]?.file_id).toBe('B'));
    const chipsB = store.uploadedFiles;
    responseA.resolve(fileList('A'));
    await syncingA;
    expect(store.currentSession).toBe(b);
    expect(store.uploadedFiles).toBe(chipsB);
    expect(uploadApi.listFiles).toHaveBeenNthCalledWith(1, 'server-A');
    expect(uploadApi.listFiles).toHaveBeenNthCalledWith(2, 'server-B');
  });

  it('ignores a late A list failure without restoring A local refs over B chips', async () => {
    const { store, b } = buildStore();
    const responseA = deferred<FileListResponse>();
    vi.mocked(uploadApi.listFiles).mockReturnValueOnce(responseA.promise).mockResolvedValueOnce(fileList('B'));
    store.setUploadedFiles([projectRef('A')]);
    const syncingA = store.syncUploadedFilesFromServer();
    store.setCurrentSession(b);
    store.setUploadedFiles([projectRef('B-reference')]);
    await vi.waitFor(() => expect(store.uploadedFiles.some((file: UploadedFile) => file.file_id === 'B')).toBe(true));
    const chipsB = store.uploadedFiles;
    responseA.reject(new Error('A list failed'));
    await syncingA;
    expect(store.uploadedFiles).toBe(chipsB);
    expect(store.uploadedFiles.map((file: UploadedFile) => file.file_id)).toEqual(['project_B-reference', 'B']);
  });

  it('applies the newest same-session list and ignores an older successful response', async () => {
    const { store } = buildStore();
    const oldResponse = deferred<FileListResponse>();
    const newResponse = deferred<FileListResponse>();
    vi.mocked(uploadApi.listFiles).mockReturnValueOnce(oldResponse.promise).mockReturnValueOnce(newResponse.promise);
    const oldSync = store.syncUploadedFilesFromServer();
    const newSync = store.syncUploadedFilesFromServer();
    store.setUploadedFiles([projectRef('added-during-fetch')]);
    newResponse.resolve(fileList('A', 'latest'));
    await newSync;
    const latestChips = store.uploadedFiles;
    oldResponse.resolve(fileList('A', 'old'));
    await oldSync;
    expect(store.uploadedFiles).toBe(latestChips);
    expect(store.uploadedFiles.map((file: UploadedFile) => file.file_id)).toEqual(['project_added-during-fetch', 'latest']);
  });

  it('ignores an older same-session failure after a newer list succeeded', async () => {
    const { store } = buildStore();
    const oldResponse = deferred<FileListResponse>();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(uploadApi.listFiles).mockReturnValueOnce(oldResponse.promise).mockResolvedValueOnce(fileList('A', 'latest'));
    const oldSync = store.syncUploadedFilesFromServer();
    await store.syncUploadedFilesFromServer();
    const latestChips = store.uploadedFiles;
    oldResponse.reject(new Error('Old request failed'));
    await oldSync;
    expect(store.uploadedFiles).toBe(latestChips);
    expect(warn).not.toHaveBeenCalled();
  });

  it('preserves current chips and refs added during a failed current request', async () => {
    const { store } = buildStore();
    const response = deferred<FileListResponse>();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(uploadApi.listFiles).mockReturnValueOnce(response.promise);
    store.setUploadedFiles([projectRef('initial')]);
    const syncing = store.syncUploadedFilesFromServer();
    store.setUploadedFiles([projectRef('new')]);
    const currentChips = store.uploadedFiles;
    response.reject(new Error('Current list unavailable'));
    await syncing;
    expect(store.uploadedFiles).toBe(currentChips);
    expect(store.uploadedFiles[0].file_id).toBe('project_new');
  });
});
