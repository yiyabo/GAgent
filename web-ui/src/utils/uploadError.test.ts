import { AxiosError } from 'axios';
import { beforeEach, describe, expect, it } from 'vitest';
import { describeUploadError } from './uploadError';
import { useLanguageStore } from '@/i18n/language';

function httpError(status: number, data: unknown): AxiosError {
  return new AxiosError(
    `Request failed with status code ${status}`,
    'ERR_BAD_REQUEST',
    undefined,
    undefined,
    { status, data, statusText: '', headers: {}, config: {} as never },
  );
}

describe('describeUploadError', () => {
  beforeEach(() => useLanguageStore.getState().setLanguage('en'));

  it('says the file is too large instead of printing the status code', () => {
    const plain = describeUploadError(httpError(413, '<html>413 Request Entity Too Large</html>'), 'upload');
    expect(plain).toMatch(/too large/);
    expect(plain).not.toMatch(/status code/);
    expect(describeUploadError(httpError(413, { detail: 'File is larger than the 2048 MB upload limit' }), 'upload'))
      .toMatch(/2048 MB upload limit/);
  });

  it('shows the server reason for a rejected file and names the status for server errors', () => {
    expect(describeUploadError(httpError(400, { detail: 'unsupported file type: application/x-foo' }), 'upload'))
      .toBe('The server rejected the file: unsupported file type: application/x-foo');
    expect(describeUploadError(httpError(500, { detail: 'disk full' }), 'upload')).toMatch(/HTTP 500.*disk full/);
    expect(describeUploadError(httpError(503, '<html/>'), 'upload')).toMatch(/HTTP 503/);
    expect(describeUploadError(httpError(418, undefined), 'upload')).toMatch(/HTTP 418/);
  });

  it('explains sign-in, permission and missing-file cases', () => {
    expect(describeUploadError(httpError(401, {}), 'upload')).toMatch(/sign in again/);
    expect(describeUploadError(httpError(403, {}), 'remove')).toMatch(/permission/);
    expect(describeUploadError(httpError(404, { detail: 'File not found' }), 'remove')).toMatch(/no longer on the server/);
  });

  it('tells a network failure from a timeout, in both languages', () => {
    expect(describeUploadError(new AxiosError('Network Error', 'ERR_NETWORK'), 'upload')).toMatch(/Cannot reach the server/);
    expect(describeUploadError(new AxiosError('timeout', 'ECONNABORTED'), 'upload')).toMatch(/took too long/);
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(describeUploadError(httpError(413, ''), 'upload')).toMatch(/文件太大/);
    expect(describeUploadError(new Error('本地错误'), 'upload')).toBe('本地错误');
  });
});
