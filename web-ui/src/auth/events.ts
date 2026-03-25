export const AUTH_UNAUTHORIZED_EVENT = 'ga:auth-unauthorized';

export const emitAuthUnauthorized = (): void => {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT));
};
