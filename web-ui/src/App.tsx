import { Suspense, useEffect } from 'react';
import { App as AntdApp, Layout, Spin } from 'antd';
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { checkApiHealth } from '@api/client';
import { AUTH_UNAUTHORIZED_EVENT } from '@/auth/events';
import AppHeader from '@components/layout/AppHeader';
import ErrorBoundary from '@components/common/ErrorBoundary';
import { useAuthStore } from '@store/auth';
import { useSystemStore } from '@store/system';
import { ENV } from '@/config/env';
import { retryLazy } from '@/utils/retryLazy';

const ChatLayout = retryLazy(() => import('@components/layout/ChatLayout'));
const Dashboard = retryLazy(() => import('@pages/Dashboard'));
const Tasks = retryLazy(() => import('@pages/Tasks'));
const Plans = retryLazy(() => import('@pages/Plans'));
const Memory = retryLazy(() => import('@pages/Memory'));
const System = retryLazy(() => import('@pages/System'));
const Login = retryLazy(() => import('@pages/Login'));
const Register = retryLazy(() => import('@pages/Register'));

const FullPageLoading = () => (
  <div
    style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-primary)',
    }}
  >
    <Spin size="large" />
  </div>
);

const RequireAuth = () => {
  const { initialized, authenticated, legacyAccessAllowed } = useAuthStore();
  const location = useLocation();
  if (!initialized) {
    return <FullPageLoading />;
  }
  if (!authenticated && !legacyAccessAllowed) {
    const next = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <Outlet />;
};

const GuestOnly = ({ children }: { children: JSX.Element }) => {
  const { initialized, authenticated } = useAuthStore();
  const location = useLocation();
  if (!initialized) {
    return <FullPageLoading />;
  }
  if (authenticated) {
    const next = new URLSearchParams(location.search).get('next') || '/chat';
    return <Navigate to={next} replace />;
  }
  return children;
};

const ProtectedLayout = () => (
  <ErrorBoundary>
    <Layout style={{ minHeight: '100vh' }}>
      <AppHeader />
      <Layout>
        <Layout.Content style={{ padding: '24px', background: '#f0f2f5' }}>
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </Layout.Content>
      </Layout>
    </Layout>
  </ErrorBoundary>
);

const RouteContent = ({ children }: { children: JSX.Element }) => (
  <ErrorBoundary>
    <Suspense fallback={<FullPageLoading />}>
      {children}
    </Suspense>
  </ErrorBoundary>
);

function App() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const { setSystemStatus, setApiConnected } = useSystemStore();
  const { bootstrap, clearAuth } = useAuthStore();

  useEffect(() => {
    const handleError = (event: ErrorEvent) => {
      console.error('Unhandled error:', event.error || event.message);
      message.error(`error: ${event.message || 'error'}`, 6);
    };
    const handleRejection = (event: PromiseRejectionEvent) => {
      const reason = (event.reason && (event.reason.message || event.reason.toString())) || 'reason';
      console.error('Unhandled rejection:', event.reason);
      message.error(`exception: ${reason}`, 6);
    };
    const handleUnauthorized = () => {
      clearAuth();
      const currentPath = window.location.pathname;
      if (!currentPath.startsWith('/login') && !currentPath.startsWith('/register')) {
        navigate('/login', { replace: true });
      }
    };

    window.addEventListener('error', handleError);
    window.addEventListener('unhandledrejection', handleRejection);
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);

    const initializeApp = async () => {
      console.log('🚀 Initializing AI Task Orchestration System...');
      console.log('⚡ Running in PRODUCTION mode - using REAL APIs (No Mock)');

      await bootstrap();

      try {
        await new Promise(resolve => setTimeout(resolve, 500));

        const healthData = await checkApiHealth();
        console.log('🏥 Health check result:', healthData);

        setApiConnected(healthData.api_connected);
        setSystemStatus({
          api_connected: healthData.api_connected,
          database_status: healthData.api_connected ? 'connected' : 'disconnected',
          active_tasks: 0,
          total_plans: 0,
          system_load: {
            cpu: 0,
            memory: 0,
            api_calls_per_minute: 0,
          },
        });

        if (!healthData.api_connected) {
          message.error(`Backend service connection failed. Please check: ${ENV.API_BASE_URL}`, 10);
          setApiConnected(false);
        }
      } catch (error) {
        console.error('❌ App initialization failed:', error);
        message.error('System initialization failed. Please check network and backend service.', 10);
        setApiConnected(false);
        setSystemStatus({
          api_connected: false,
          database_status: 'disconnected',
          active_tasks: 0,
          total_plans: 0,
          system_load: {
            cpu: 0,
            memory: 0,
            api_calls_per_minute: 0,
          },
        });
      }
    };

    void initializeApp();

    return () => {
      window.removeEventListener('error', handleError);
      window.removeEventListener('unhandledrejection', handleRejection);
      window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
    };
  }, [bootstrap, clearAuth, message, navigate, setApiConnected, setSystemStatus]);

  return (
    <Routes>
      <Route
        path="/login"
        element={(
          <GuestOnly>
            <RouteContent>
              <Login />
            </RouteContent>
          </GuestOnly>
        )}
      />
      <Route
        path="/register"
        element={(
          <GuestOnly>
            <RouteContent>
              <Register />
            </RouteContent>
          </GuestOnly>
        )}
      />
      <Route element={<RequireAuth />}>
        <Route element={<ProtectedLayout />}>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/dashboard" element={<RouteContent><Dashboard /></RouteContent>} />
          <Route path="/chat" element={<RouteContent><ChatLayout /></RouteContent>} />
          <Route path="/tasks" element={<RouteContent><Tasks /></RouteContent>} />
          <Route path="/plans" element={<RouteContent><Plans /></RouteContent>} />
          <Route path="/memory" element={<RouteContent><Memory /></RouteContent>} />
          <Route path="/system" element={<RouteContent><System /></RouteContent>} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
