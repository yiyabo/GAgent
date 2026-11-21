import { AxiosError } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CHUNKED_UPLOAD_THRESHOLD_BYTES,
  UPLOAD_CHUNK_ATTEMPTS,
  UPLOAD_CHUNK_BYTES,
  UploadApi,
  uploadRetryTiming,
} from './upload';

function networkError(): AxiosError {
  return new AxiosError('Network Error', 'ERR_NETWORK');
}

function statusError(status: number): AxiosError {
  const error = new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE');
  error.response = { status, data: {}, statusText: '', headers: {}, config: {} as never };
  return error;
}

function fileOfSize(size: number, name = 'A514_R2.fq.gz'): File {
  return { name, size, slice: (start: number, end: number) => new Blob([new Uint8Array(end - start)]) } as unknown as File;
}

function apiWith(post: ReturnType<typeof vi.fn>): UploadApi {
  const api = new UploadApi();
  (api as unknown as { client: { post: typeof post } }).client = { post };
  return api;
}

describe('uploadApi.uploadFile', () => {
  const delay = uploadRetryTiming.delay;
  beforeEach(() => {
    uploadRetryTiming.delay = vi.fn().mockResolvedValue(undefined);
  });
  afterEach(() => {
    uploadRetryTiming.delay = delay;
  });

  it('sends a small file in one request', async () => {
    const post = vi.fn().mockResolvedValue({ data: { file_id: 'one' } });
    const result = await apiWith(post).uploadFile(fileOfSize(1024, 'a.fasta'), 'session_1');
    expect(result).toEqual({ file_id: 'one' });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe('/upload/file');
  });

  it('sends a large file in chunks and re-sends only the chunk whose connection dropped', async () => {
    const size = CHUNKED_UPLOAD_THRESHOLD_BYTES + UPLOAD_CHUNK_BYTES + 5;
    const chunks = Math.ceil(size / UPLOAD_CHUNK_BYTES);
    let failedOnce = false;
    const post = vi.fn(async (url: string, form: FormData) => {
      if (url === '/upload/chunk' && form.get('index') === '2' && !failedOnce) {
        failedOnce = true;
        throw networkError();
      }
      return { data: url === '/upload/chunk/complete' ? { file_id: 'joined' } : { success: true } };
    });
    const progress: number[] = [];
    const result = await apiWith(post).uploadFile(fileOfSize(size), 'session_1', (f) => progress.push(f));

    expect(result).toEqual({ file_id: 'joined' });
    const chunkCalls = post.mock.calls.filter(([url]) => url === '/upload/chunk');
    expect(chunkCalls).toHaveLength(chunks + 1);
    expect(chunkCalls.map(([, form]) => (form as FormData).get('index'))).toEqual(
      ['0', '1', '2', '2', ...Array.from({ length: chunks - 3 }, (_, i) => String(i + 3))],
    );
    const uploadIds = new Set(post.mock.calls.map(([, form]) => (form as FormData).get('upload_id')));
    expect(uploadIds.size).toBe(1);
    const complete = post.mock.calls.at(-1)!;
    expect(complete[0]).toBe('/upload/chunk/complete');
    expect((complete[1] as FormData).get('total')).toBe(String(chunks));
    expect((complete[1] as FormData).get('total_size')).toBe(String(size));
    expect(progress.at(-1)).toBe(1);
    expect(uploadRetryTiming.delay).toHaveBeenCalledTimes(1);
  });

  it('gives up after the retry budget and does not retry a rejected chunk', async () => {
    const size = CHUNKED_UPLOAD_THRESHOLD_BYTES + 1;
    const flaky = vi.fn().mockRejectedValue(statusError(502));
    await expect(apiWith(flaky).uploadFile(fileOfSize(size), 'session_1')).rejects.toBeInstanceOf(AxiosError);
    expect(flaky).toHaveBeenCalledTimes(UPLOAD_CHUNK_ATTEMPTS);

    const rejected = vi.fn().mockRejectedValue(statusError(413));
    await expect(apiWith(rejected).uploadFile(fileOfSize(size), 'session_1')).rejects.toBeInstanceOf(AxiosError);
    expect(rejected).toHaveBeenCalledTimes(1);
  });
});
