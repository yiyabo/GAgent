import ReactDOM from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp, ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import { queryClient } from '@/queryClient';
import { emitAuthUnauthorized } from '@/auth/events';
import './styles/index.css';

declare global {
  interface Window {
    __gaPatchedFetch?: boolean;
  }

  interface RequestInit {
    skipAuthHandling?: boolean;
  }
}

const antdTheme = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#1890ff',
    colorSuccess: '#52c41a',
    colorWarning: '#faad14',
    colorError: '#ff4d4f',
    borderRadius: 6,
    fontSize: 14,
  },
  components: {
    Layout: {
      headerBg: '#001529',
      siderBg: '#001529',
    },
    Menu: {
      darkItemBg: '#001529',
      darkSubMenuItemBg: '#000c17',
    },
  },
};

const router = createBrowserRouter([
  {
    path: '/*',
    element: <App />,
  },
]);

if (typeof window !== 'undefined' && !window.__gaPatchedFetch) {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const skipAuthHandling = Boolean(init?.skipAuthHandling);
    const response = await nativeFetch(input, {
      ...init,
      credentials: init?.credentials ?? 'include',
    });
    if (response.status === 401 && !skipAuthHandling) {
      emitAuthUnauthorized();
    }
    return response;
  };
  window.__gaPatchedFetch = true;
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={queryClient}>
    <ConfigProvider locale={zhCN} theme={antdTheme}>
      <AntdApp>
        <RouterProvider router={router} />
      </AntdApp>
    </ConfigProvider>
  </QueryClientProvider>
);
