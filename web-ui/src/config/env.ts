/**
 * configuration
 *
 * must VITE_  Vite 
 * configurationfile: 
 * - : web-ui/.env.development
 * - : web-ui/.env.production
 */

const browserHostname =
  typeof window !== 'undefined' && window.location.hostname
    ? window.location.hostname
    : 'localhost';

const browserProtocol =
  typeof window !== 'undefined' && window.location.protocol
    ? window.location.protocol
    : 'http:';

const defaultApiProtocol = browserProtocol === 'https:' ? 'https:' : 'http:';
const defaultWsProtocol = browserProtocol === 'https:' ? 'wss:' : 'ws:';

const defaultApiBaseUrl = `${defaultApiProtocol}//${browserHostname}:9000`;
const defaultWsBaseUrl = `${defaultWsProtocol}//${browserHostname}:9000`;

const rawApiUrl = import.meta.env.VITE_API_BASE_URL;
const rawWsUrl = import.meta.env.VITE_WS_BASE_URL;

export const ENV = {
  API_BASE_URL: rawApiUrl !== undefined
    ? rawApiUrl
    : (import.meta.env.DEV ? '' : defaultApiBaseUrl),

  WS_BASE_URL: rawWsUrl !== undefined
    ? rawWsUrl
    : (import.meta.env.DEV ? '' : defaultWsBaseUrl),

  DEV_SERVER_PORT: import.meta.env.VITE_DEV_SERVER_PORT || 3000,

  isDevelopment: import.meta.env.DEV,

  isProduction: import.meta.env.PROD,

  mode: import.meta.env.MODE,
};

if (ENV.isDevelopment) {
  console.log('🌍 Environment Configuration:', {
  API_BASE_URL: ENV.API_BASE_URL,
  WS_BASE_URL: ENV.WS_BASE_URL,
  mode: ENV.mode,
  });
}

declare global {
  interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_BASE_URL?: string;
  readonly VITE_DEV_SERVER_PORT?: string;
  /** UI language before a visitor picks one: 'zh-CN' or 'en'. A deployment setting; see i18n/language.ts. */
  readonly VITE_DEFAULT_LANGUAGE?: string;
  }

  interface ImportMeta {
  readonly env: ImportMetaEnv;
  }
}

export default ENV;
