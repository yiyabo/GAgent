import ReactDOM from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { App as AntdApp } from 'antd';
import App from './App';
import { queryClient } from '@/queryClient';
import { emitAuthUnauthorized } from '@/auth/events';
import '@/i18n';
import ThemeProvider from '@/theme/ThemeProvider';
import './styles/fonts.css';
import './styles/tokens.css';
import './styles/index.css';

declare global {
  interface Window {
    __gaPatchedFetch?: boolean;
  }

  interface RequestInit {
    skipAuthHandling?: boolean;
  }
}

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
    <ThemeProvider>
      <AntdApp>
        <RouterProvider router={router} />
      </AntdApp>
    </ThemeProvider>
  </QueryClientProvider>
);
