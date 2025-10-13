const DEFAULT_API_PORT = 9000;

const inferDefaultApiBaseUrl = () => {
  if (typeof window === 'undefined' || !window.location) {
    return `http://localhost:${DEFAULT_API_PORT}`;
  }

  const protocol = window.location.protocol === 'https:' ? 'https' : 'http';
  const hostname = window.location.hostname || 'localhost';
  return `${protocol}://${hostname}:${DEFAULT_API_PORT}`;
};

const cleanBaseUrl = (url: string) => url.replace(/\/+$/, '');

const apiBaseUrl = cleanBaseUrl(
  import.meta.env.VITE_API_BASE_URL?.trim() || inferDefaultApiBaseUrl()
);

const wsOverride = import.meta.env.VITE_WS_BASE_URL?.trim();

const wsBaseUrl = wsOverride
  ? cleanBaseUrl(wsOverride)
  : apiBaseUrl.replace(/^http/i, (protocol) =>
      protocol.toLowerCase() === 'https' ? 'wss' : 'ws'
    );

export const envConfig = {
  apiBaseUrl,
  wsBaseUrl,
};
