/**
 * 前端环境变量统一配置
 *
 * 所有环境变量都必须以 VITE_ 开头才能被 Vite 暴露给浏览器
 * 配置文件位置：
 * - 开发环境: web-ui/.env.development
 * - 生产环境: web-ui/.env.production
 */

export const ENV = {
  // ===== API 配置 =====
  /** 后端 API 基础地址 */
  API_BASE_URL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:9000',

  /** WebSocket 基础地址 */
  WS_BASE_URL: import.meta.env.VITE_WS_BASE_URL || 'ws://localhost:9000',

  // ===== 前端服务器配置 =====
  /** 前端开发服务器端口 */
  DEV_SERVER_PORT: import.meta.env.VITE_DEV_SERVER_PORT || 3000,

  // ===== 环境标识 =====
  /** 是否为开发环境 */
  isDevelopment: import.meta.env.DEV,

  /** 是否为生产环境 */
  isProduction: import.meta.env.PROD,

  /** 当前模式 (development | production | test) */
  mode: import.meta.env.MODE,
};

// 开发环境下输出配置信息（方便调试）
if (ENV.isDevelopment) {
  console.log('🌍 Environment Configuration:', {
    API_BASE_URL: ENV.API_BASE_URL,
    WS_BASE_URL: ENV.WS_BASE_URL,
    mode: ENV.mode,
  });
}

// 类型声明（为了 TypeScript 类型检查）
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
