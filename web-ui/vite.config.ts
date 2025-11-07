import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // 统一从仓库根目录加载 .env（确保前后端共享同一份配置）
  const projectRoot = path.resolve(__dirname, '..');
  const env = loadEnv(mode, projectRoot, '');

  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://localhost:9000';
  const wsBaseUrl = env.VITE_WS_BASE_URL || 'ws://localhost:9000';
  const devServerPort = Number(env.VITE_DEV_SERVER_PORT) || 3000;
  const devServerHost = env.VITE_DEV_SERVER_HOST || '0.0.0.0';

  return {
    envDir: projectRoot,
    plugins: [react()],
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
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom', 'react-router-dom'],
            antd: ['antd', '@ant-design/icons'],
            visualization: ['vis-network', 'vis-data'],
            editor: ['monaco-editor', '@monaco-editor/react'],
          },
        },
      },
    },
  };
});
