import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs/promises';
import path from 'path';
import viteCompression from 'vite-plugin-compression';

/**
 * Puts the deployment's default UI language into index.html (title, description, <html lang>) and a
 * pre-paint script that lets a visitor's saved choice win. Mirrors src/i18n/language.ts; the page
 * title must be right before React boots, because the tab shows it first.
 */
const htmlLanguage = (rawDefault: string | undefined) => {
  const fallback = rawDefault === 'zh-CN' ? 'zh-CN' : 'en';
  const copy = {
    en: { title: 'Phage-Agent · Research workspace', description: 'Phage-Agent research workspace: research chat, plan execution and traceable artifacts.' },
    'zh-CN': { title: 'Phage-Agent · 研究工作台', description: 'Phage-Agent 科研工作台：研究对话、计划执行与可追溯产物。' },
  } as const;
  return {
    name: 'html-language',
    transformIndexHtml(html: string) {
      const chosen = copy[fallback];
      const script = `<script>
      // Language before first paint: a saved choice (gagent.language) beats the deployment default (${fallback}).
      (function () {
        try {
          var copy = ${JSON.stringify(copy)};
          var saved = localStorage.getItem('gagent.language');
          var lang = saved === 'zh-CN' || saved === 'en' ? saved : '${fallback}';
          document.documentElement.setAttribute('lang', lang);
          document.title = copy[lang].title;
          var meta = document.querySelector('meta[name="description"]');
          if (meta) meta.setAttribute('content', copy[lang].description);
        } catch (e) {}
      })();
    </script>`;
      return html
        .replace(/<html lang="[^"]*">/, `<html lang="${fallback}">`)
        .replace(/<title>[^<]*<\/title>/, `<title>${chosen.title}</title>`)
        .replace(/(<meta name="description" content=")[^"]*(")/, `$1${chosen.description}$2`)
        .replace('</head>', `${script}\n  </head>`);
    },
  };
};

const patchThirdPartyModules = () => {
  const directiveRe = /^\s*(["'])use client\1;?\s*/;
  return {
    name: 'patch-third-party-modules',
    enforce: 'pre' as const,
    transform(code: string, id: string) {
      const cleanId = (id || '').split('?')[0];
      if (!cleanId.includes('node_modules')) return null;
      if (!/\.(m?js|cjs|jsx|ts|tsx)$/.test(cleanId)) return null;

      let next = code;

      // Vite 4 + some deps (antd, @ant-design/icons) ship with `"use client";` which can
      // break Vite import analysis in certain environments. It is a no-op in Vite apps.
      if (directiveRe.test(next)) {
        next = next.replace(directiveRe, '');
      }

      // Work around a broken publish of rc-input-number where the Chinese IME replacement
      // regex was stripped, resulting in invalid JS: `inputStr.replace(//g, '.')`.
      if (cleanId.includes('/rc-input-number/') && /InputNumber\.(m?js|cjs)$/.test(cleanId)) {
        const broken = 'inputStr.replace(//g,';
        if (next.includes(broken)) {
          next = next.split(broken).join("inputStr.replace(/[．]/g,");
        }
      }

      if (next === code) return null;
      return { code: next, map: null };
    },
  };
};

const patchThirdPartyModulesForOptimizeDeps = () => {
  const directiveRe = /^\s*(["'])use client\1;?\s*/;
  const targetFilter =
    /[\\/]node_modules[\\/](antd|rc-input-number|@ant-design[\\/]icons)[\\/].*\.(m?js|cjs)$/;

  return {
    name: 'patch-third-party-modules-optimize-deps',
    setup(build: any) {
      build.onLoad({ filter: targetFilter }, async (args: any) => {
        const source = await fs.readFile(args.path, 'utf8');
        let next = source;

        if (directiveRe.test(next)) {
          next = next.replace(directiveRe, '');
        }

        const broken = 'inputStr.replace(//g,';
        if (args.path.includes(`${path.sep}rc-input-number${path.sep}`) && next.includes(broken)) {
          next = next.split(broken).join("inputStr.replace(/[．]/g,");
        }

        return {
          contents: next,
          loader: 'js',
        };
      });
    },
  };
};

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  //  .env（）
  const projectRoot = path.resolve(__dirname, '..');
  const env = loadEnv(mode, projectRoot, '');

  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://localhost:9000';
  const wsBaseUrl = env.VITE_WS_BASE_URL || 'ws://localhost:9000';
  const devServerPort = Number(env.VITE_DEV_SERVER_PORT) || 3001;
  const devServerHost = env.VITE_DEV_SERVER_HOST || '0.0.0.0';

  return {
    envDir: projectRoot,
    esbuild: {
      drop: mode === 'production' ? ['console', 'debugger'] : [],
    },
    plugins: [
      patchThirdPartyModules(),
      react(),
      htmlLanguage(process.env.VITE_DEFAULT_LANGUAGE ?? env.VITE_DEFAULT_LANGUAGE),
      viteCompression({ algorithm: 'gzip', ext: '.gz' }),
      viteCompression({ algorithm: 'brotliCompress', ext: '.br' }),
    ],
    optimizeDeps: {
      esbuildOptions: {
        plugins: [patchThirdPartyModulesForOptimizeDeps()],
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
        '@components': path.resolve(__dirname, './src/components'),
        '@pages': path.resolve(__dirname, './src/pages'),
        '@hooks': path.resolve(__dirname, './src/hooks'),
        '@utils': path.resolve(__dirname, './src/utils'),
        '@types': path.resolve(__dirname, './src/types'),
        '@api': path.resolve(__dirname, './src/api'),
        '@store': path.resolve(__dirname, './src/store'),
      },
    },
    server: {
      host: devServerHost,
      port: devServerPort,
      strictPort: true,
      allowedHosts: ['bioagent.byoryn.cn'],
      watch: {
        // Exclude large data directories from fs watching to avoid ENOSPC
        ignored: [
          '**/phagescope/**',
          '**/runtime/**',
          '**/results/**',
          '**/data/**',
          '**/log/**',
          '**/.git/**',
        ],
      },
      proxy: {
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/artifacts': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/ws': {
          target: wsBaseUrl,
          ws: true,
          changeOrigin: true,
        },
        '/sso': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/auth': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/health': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/chat/sessions': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/chat/runs': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/chat/stream': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/chat/actions': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/chat/history': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/jobs': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/artifacts': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/plans/': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/tasks/': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/upload': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/project': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: devServerHost,
      port: devServerPort,
      strictPort: true,
      proxy: {
        '/api': { target: apiBaseUrl, changeOrigin: true },
        '/ws': { target: wsBaseUrl, ws: true, changeOrigin: true },
        '/sso': { target: apiBaseUrl, changeOrigin: true },
        '/auth': { target: apiBaseUrl, changeOrigin: true },
        '/health': { target: apiBaseUrl, changeOrigin: true },
        '/chat/sessions': { target: apiBaseUrl, changeOrigin: true },
        '/chat/runs': { target: apiBaseUrl, changeOrigin: true },
        '/chat/stream': { target: apiBaseUrl, changeOrigin: true },
        '/chat/actions': { target: apiBaseUrl, changeOrigin: true },
        '/chat/history': { target: apiBaseUrl, changeOrigin: true },
        '/jobs': { target: apiBaseUrl, changeOrigin: true },
        '/artifacts': { target: apiBaseUrl, changeOrigin: true },
        '/plans/': { target: apiBaseUrl, changeOrigin: true },
        '/tasks/': { target: apiBaseUrl, changeOrigin: true },
        '/upload': { target: apiBaseUrl, changeOrigin: true },
        '/project': { target: apiBaseUrl, changeOrigin: true },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      chunkSizeWarningLimit: 700,
      minify: 'esbuild',
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) {
              return undefined;
            }
            if (
              id.includes('/react/') ||
              id.includes('/react-dom/') ||
              id.includes('/react-router-dom/')
            ) {
              return 'vendor';
            }
            if (
              id.includes('/vis-network/') ||
              id.includes('/vis-data/')
            ) {
              return 'visualization-2d';
            }
            if (
              id.includes('/react-force-graph-2d/') ||
              id.includes('/react-force-graph-3d/') ||
              id.includes('/three/') ||
              id.includes('/three-spritetext/')
            ) {
              return 'visualization-3d';
            }
            if (
              id.includes('/react-markdown/') ||
              id.includes('/remark-gfm/') ||
              id.includes('/remark-math/') ||
              id.includes('/rehype-katex/')
            ) {
              return 'markdown-core';
            }
            if (
              id.includes('/react-syntax-highlighter/') ||
              id.includes('/prismjs/')
            ) {
              return 'markdown-code';
            }
            if (id.includes('/katex/')) {
              return 'markdown-math';
            }
            return undefined;
          },
        },
      },
    },
  };
});
