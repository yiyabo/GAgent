import axios from 'axios';
import { translate } from '@/i18n/useT';
import { serverDetail } from './authError';

export type UploadAction = 'upload' | 'remove';

/**
 * One sentence that says why an upload or a removal failed. axios' own message ("Request failed with
 * status code 413") does not tell the user what to change, so known statuses get a written reason and
 * anything else shows what the server said together with the status code.
 */
export function describeUploadError(error: unknown, action: UploadAction): string {
  const t = translate;
  if (!axios.isAxiosError(error)) {
    const message = error instanceof Error ? error.message.trim() : '';
    return message || t('upload.error.unknown', { status: '?' });
  }

  if (!error.response) {
    return error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT'
      ? t('upload.error.timeout')
      : t('upload.error.network');
  }

  const status = error.response.status;
  const detail = serverDetail(error);

  if (status === 413) return detail ? t('upload.error.tooLargeDetail', { detail }) : t('upload.error.tooLarge');
  if (status === 401) return t('upload.error.sessionExpired');
  if (status === 403) return t('upload.error.forbidden');
  if (status === 404) return action === 'remove' ? t('upload.error.alreadyGone') : t('upload.error.notFound');
  if (status === 400 || status === 415 || status === 422) {
    return detail ? t('upload.error.rejected', { detail }) : t('upload.error.rejectedNoDetail');
  }
  if (status === 502 || status === 503 || status === 504) return t('upload.error.unavailable', { status });
  if (status >= 500) {
    return detail ? t('upload.error.serverDetail', { status, detail }) : t('upload.error.server', { status });
  }
  return detail ? t('upload.error.unknownWithDetail', { status, detail }) : t('upload.error.unknown', { status });
}
