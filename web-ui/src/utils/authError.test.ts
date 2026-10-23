import { AxiosError } from 'axios';
import { beforeEach, describe, expect, it } from 'vitest';
import { describeAuthError, serverDetail } from './authError';
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

describe('describeAuthError', () => {
  beforeEach(() => useLanguageStore.getState().setLanguage('en'));

  it('explains a closed registration instead of printing the bare status code', () => {
    const message = describeAuthError(httpError(403, { detail: 'Open signup is disabled.' }), 'register');
    expect(message).toMatch(/Registration is closed/);
    expect(message).toMatch(/administrator/);
    expect(message).not.toMatch(/status code/);
  });

  it('says the same thing in Chinese when that is the chosen language', () => {
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(describeAuthError(httpError(403, { detail: 'Open signup is disabled.' }), 'register')).toMatch(/自助注册/);
    expect(describeAuthError(httpError(401, { detail: 'Invalid email or password.' }), 'login')).toBe('邮箱或密码不正确。');
  });

  it('maps the other statuses the auth endpoints return', () => {
    expect(describeAuthError(httpError(401, { detail: 'Invalid email or password.' }), 'login')).toBe('Wrong email or password.');
    expect(describeAuthError(httpError(401, { detail: 'Current password is incorrect.' }), 'changePassword')).toBe(
      'The current password is incorrect.',
    );
    expect(describeAuthError(httpError(409, { detail: 'Email is already registered.' }), 'register')).toMatch(/already registered/);
    expect(describeAuthError(httpError(403, {}), 'login')).toMatch(/permission/);
    expect(describeAuthError(httpError(429, {}), 'login')).toMatch(/Too many attempts/);
  });

  it('shows what the server said for a rejected request, including validation lists', () => {
    expect(describeAuthError(httpError(400, { detail: 'Password must be at least 8 characters.' }), 'register')).toBe(
      'The server rejected the request: Password must be at least 8 characters.',
    );
    const validation = httpError(422, { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email address' }] });
    expect(serverDetail(validation)).toBe('value is not a valid email address');
    expect(describeAuthError(validation, 'register')).toMatch(/not a valid email/);
    expect(describeAuthError(httpError(400, undefined), 'register')).toMatch(/Check the email and password/);
  });

  it('names the status for outages and unknown errors so it can be reported', () => {
    expect(describeAuthError(httpError(503, '<html>503</html>'), 'login')).toMatch(/HTTP 503/);
    expect(describeAuthError(httpError(500, { detail: 'boom' }), 'login')).toMatch(/HTTP 500/);
    expect(describeAuthError(httpError(418, { detail: 'teapot' }), 'login')).toBe('The request failed (HTTP 418): teapot');
    expect(describeAuthError(httpError(418, undefined), 'login')).toBe('The request failed (HTTP 418).');
  });

  it('tells a network failure from a timeout, and passes non-HTTP errors through', () => {
    expect(describeAuthError(new AxiosError('Network Error', 'ERR_NETWORK'), 'login')).toMatch(/Cannot reach the server/);
    expect(describeAuthError(new AxiosError('timeout of 30000ms exceeded', 'ECONNABORTED'), 'login')).toMatch(/did not answer in time/);
    expect(describeAuthError(new Error('something local'), 'login')).toBe('something local');
  });
});
