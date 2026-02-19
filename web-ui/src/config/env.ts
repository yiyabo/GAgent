/**
 * configuration
 *
 * must VITE_  Vite 
 * configurationfile: 
 * - : web-ui/.env.development
 * - : web-ui/.env.production
 */

export const ENV = {
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:9000',

  WS_BASE_URL: import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:9000',

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
  }

  interface ImportMeta {
  readonly env: ImportMetaEnv;
  }
}

export default ENV;
