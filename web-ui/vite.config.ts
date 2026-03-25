import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs/promises';
import path from 'path';

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
  const devServerPort = Number(env.VITE_DEV_SERVER_PORT) || 3000;
  const devServerHost = env.VITE_DEV_SERVER_HOST || '0.0.0.0';

  return {
    envDir: projectRoot,
    plugins: [patchThirdPartyModules(), react()],
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
      proxy: {
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/ws': {
          target: wsBaseUrl,
          ws: true,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      chunkSizeWarningLimit: 700,
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
              id.includes('/monaco-editor/') ||
              id.includes('/@monaco-editor/')
            ) {
              return 'editor';
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
            if (
              id.includes('/xterm/') ||
              id.includes('/xterm-addon-fit/') ||
              id.includes('/xterm-addon-web-links/')
            ) {
              return 'terminal';
            }
            return undefined;
          },
        },
      },
    },
  };
});
