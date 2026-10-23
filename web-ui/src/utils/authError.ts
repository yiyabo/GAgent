import axios from 'axios';
import { translate } from '@/i18n/useT';

export type AuthAction = 'login' | 'register' | 'changePassword';

/**
 * The reason the server wrote in the response body, when it wrote one. FastAPI sends `detail` either
 * as a string (HTTPException) or as a list of `{loc, msg}` objects (request validation).
 */
export function serverDetail(error: unknown): string {
  if (!axios.isAxiosError(error)) return '';
  const body: any = error.response?.data;
  if (!body || typeof body !== 'object') return '';
  const detail = body.detail ?? body.message ?? body.error;
  if (typeof detail === 'string') return detail.trim();
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === 'object' && typeof item.msg === 'string' ? item.msg : ''))
      .filter(Boolean)
      .join('; ');
  }
  return '';
}

/**
 * One sentence the user can act on for a failed sign-in, registration or password change.
 *
 * axios' own message ("Request failed with status code 403") says nothing about why, and the pages used to
 * show exactly that. Known combinations of action and HTTP status get a written explanation; anything else
 * shows what the server said, with the status code so it can be reported.
 */
export function describeAuthError(error: unknown, action: AuthAction): string {
  const t = translate;
  if (!axios.isAxiosError(error)) {
    const message = error instanceof Error ? error.message.trim() : '';
    return message || t('auth.error.unknown', { status: '?' });
  }

  if (!error.response) {
    return error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT'
      ? t('auth.error.timeout')
      : t('auth.error.network');
  }

  const status = error.response.status;
  const detail = serverDetail(error);

  if (status === 401) {
    if (action === 'login') return t('auth.error.wrongCredentials');
    if (action === 'changePassword') return t('auth.error.wrongCurrentPassword');
    return t('auth.error.sessionExpired');
  }
  if (status === 403) {
    return action === 'register' ? t('auth.error.registerClosed') : t('auth.error.forbidden');
  }
  if (status === 409) {
    return action === 'register' ? t('auth.error.emailTaken') : detail || t('auth.error.unknown', { status });
  }
  if (status === 400 || status === 422) {
    return detail ? t('auth.error.rejected', { detail }) : t('auth.error.invalidInput');
  }
  if (status === 429) return t('auth.error.rateLimited');
  if (status === 502 || status === 503 || status === 504) return t('auth.error.unavailable', { status });
  if (status >= 500) return t('auth.error.server', { status });
  return detail ? t('auth.error.unknownWithDetail', { status, detail }) : t('auth.error.unknown', { status });
}
