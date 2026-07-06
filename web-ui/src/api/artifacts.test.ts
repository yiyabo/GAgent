import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import type { BatchDownloadEntry } from './artifacts';

vi.mock('@/config/env', () => ({
  ENV: { API_BASE_URL: 'http://api.test' },
  default: { API_BASE_URL: 'http://api.test' },
}));

import { downloadSessionBatch } from './artifacts';

const API_BASE = 'http://api.test';

const sampleFiles: BatchDownloadEntry[] = [
  { path: 'plots/a.png', scope: 'raw' },
  { path: 'report.md', scope: 'deliverables', version: 'v2' },
];

interface MockResponseOptions {
  ok?: boolean;
  status?: number;
  statusText?: string;
  contentDisposition?: string | null;
  blob?: () => Promise<Blob>;
}

function makeResponse(options: MockResponseOptions = {}) {
  const {
    ok = true,
    status = 200,
    statusText = 'OK',
    contentDisposition = null,
    blob,
  } = options;
  const headers = new Map<string, string>();
  if (contentDisposition !== null) headers.set('Content-Disposition', contentDisposition);
  return {
    ok,
    status,
    statusText,
    headers: {
      get: (name: string) => (headers.has(name) ? headers.get(name) ?? null : null),
    },
    blob: blob ?? (async () => new Blob(['zip-data'], { type: 'application/zip' })),
  };
}

describe('downloadSessionBatch', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let createObjectURLSpy: ReturnType<typeof vi.fn>;
  let revokeObjectURLSpy: ReturnType<typeof vi.fn>;
  let clickSpy: ReturnType<typeof vi.spyOn>;
  let appendChildSpy: ReturnType<typeof vi.spyOn>;
  let removeChildSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    createObjectURLSpy = vi.fn().mockReturnValue('blob:test-url');
    revokeObjectURLSpy = vi.fn();
    (URL as { createObjectURL?: (obj: Blob) => string }).createObjectURL = createObjectURLSpy;
    (URL as { revokeObjectURL?: (url: string) => void }).revokeObjectURL = revokeObjectURLSpy;

    clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);

    appendChildSpy = vi
      .spyOn(document.body, 'appendChild')
      .mockImplementation((node: Node) => node);
    removeChildSpy = vi
      .spyOn(document.body, 'removeChild')
      .mockImplementation((node: Node) => node);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete (URL as { createObjectURL?: unknown }).createObjectURL;
    delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL;
  });

  it('POSTs to the correct batch-download URL', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({ contentDisposition: 'attachment; filename="out.zip"' }),
    );
    await downloadSessionBatch('sess_123', sampleFiles);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/artifacts/sessions/sess_123/batch-download`);
    expect(init.method).toBe('POST');
  });

  it('sends JSON body { files } and application/json header', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({ contentDisposition: 'attachment; filename="out.zip"' }),
    );
    await downloadSessionBatch('sess_123', sampleFiles);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body)).toEqual({ files: sampleFiles });
    expect(init.credentials).toBe('include');
  });

  it('parses filename from Content-Disposition with quotes', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({ contentDisposition: 'attachment; filename="my-archive.zip"' }),
    );
    await downloadSessionBatch('sess_123', sampleFiles);
    const anchor = appendChildSpy.mock.calls[0][0] as HTMLAnchorElement;
    expect(anchor.download).toBe('my-archive.zip');
  });

  it('parses filename from Content-Disposition without quotes', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({ contentDisposition: 'attachment; filename=plain.zip' }),
    );
    await downloadSessionBatch('sess_123', sampleFiles);
    const anchor = appendChildSpy.mock.calls[0][0] as HTMLAnchorElement;
    expect(anchor.download).toBe('plain.zip');
  });

  it('falls back to artifacts-<sessionId>.zip when Content-Disposition is missing', async () => {
    fetchMock.mockResolvedValue(makeResponse({ contentDisposition: null }));
    await downloadSessionBatch('sess_123', sampleFiles);
    const anchor = appendChildSpy.mock.calls[0][0] as HTMLAnchorElement;
    expect(anchor.download).toBe('artifacts-sess_123.zip');
  });

  it('falls back when Content-Disposition has no filename token', async () => {
    fetchMock.mockResolvedValue(makeResponse({ contentDisposition: 'attachment' }));
    await downloadSessionBatch('sess_123', sampleFiles);
    const anchor = appendChildSpy.mock.calls[0][0] as HTMLAnchorElement;
    expect(anchor.download).toBe('artifacts-sess_123.zip');
  });

  it('creates object URL from blob, triggers download, then revokes', async () => {
    const blob = new Blob(['data'], { type: 'application/zip' });
    fetchMock.mockResolvedValue(
      makeResponse({
        blob: async () => blob,
        contentDisposition: 'attachment; filename="out.zip"',
      }),
    );
    await downloadSessionBatch('sess_123', sampleFiles);

    expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
    expect(createObjectURLSpy).toHaveBeenCalledWith(blob);

    const anchor = appendChildSpy.mock.calls[0][0] as HTMLAnchorElement;
    expect(anchor.href).toBe('blob:test-url');
    expect(anchor.download).toBe('out.zip');

    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(appendChildSpy).toHaveBeenCalledTimes(1);
    expect(removeChildSpy).toHaveBeenCalledTimes(1);
    expect(removeChildSpy.mock.calls[0][0]).toBe(anchor);

    expect(revokeObjectURLSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:test-url');
  });

  it('throws on non-2xx response with status and statusText', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({ ok: false, status: 500, statusText: 'Internal Server Error' }),
    );
    await expect(downloadSessionBatch('sess_123', sampleFiles)).rejects.toThrow(
      'Batch download failed: 500 Internal Server Error',
    );
    expect(createObjectURLSpy).not.toHaveBeenCalled();
  });
});
