import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { App as AntdApp, Layout } from 'antd';
import { useSystemStore } from '@store/system';
import { checkApiHealth } from '@api/client';
import AppHeader from '@components/layout/AppHeader';
import AppSider from '@components/layout/AppSider';
import ErrorBoundary from '@components/common/ErrorBoundary';
import ChatLayout from '@components/layout/ChatLayout';
import Dashboard from '@pages/Dashboard';
import Tasks from '@pages/Tasks';
import Plans from '@pages/Plans';
import Memory from '@pages/Memory';
import System from '@pages/System';
import { ENV } from '@/config/env';

function App() {
  const { message } = AntdApp.useApp();
  const { setSystemStatus, setApiConnected } = useSystemStore();

  // 初始化系统状态检查
  useEffect(() => {
    const handleError = (event: ErrorEvent) => {
      console.error('Unhandled error:', event.error || event.message);
      message.error(`前端运行错误: ${event.message || '未知错误'}`, 6);
    };
    const handleRejection = (event: PromiseRejectionEvent) => {
      const reason = (event.reason && (event.reason.message || event.reason.toString())) || '未知原因';
      console.error('Unhandled rejection:', event.reason);
      message.error(`前端未处理的异常: ${reason}`, 6);
    };

    window.addEventListener('error', handleError);
    window.addEventListener('unhandledrejection', handleRejection);

    const initializeApp = async () => {
      console.log('🚀 Initializing AI Task Orchestration System...');
      console.log('⚡ Running in PRODUCTION mode - using REAL APIs (No Mock)');

      try {
        // 添加延迟确保组件完全挂载
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

        if (healthData.api_connected) {
          message.success('🎉 系统连接成功！所有服务正常运行', 5);
          console.log('✅ GLM API Status:', healthData.llm_status);
        } else {
          console.error('❌ API connection failed');
          message.error(`❌ 后端服务连接失败！请检查后端是否运行在 ${ENV.API_BASE_URL}`, 10);
          setApiConnected(false);
        }
      } catch (error) {
        console.error('❌ App initialization failed:', error);
        message.error('❌ 系统初始化失败！请检查网络连接和后端服务', 10);
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

    initializeApp();

    return () => {
      window.removeEventListener('error', handleError);
      window.removeEventListener('unhandledrejection', handleRejection);
    };
  }, []);

  return (
    <ErrorBoundary>
      <Layout style={{ minHeight: '100vh' }}>
        <AppHeader />
        <Layout>
          <AppSider />
          <Layout.Content style={{ padding: '24px', background: '#f0f2f5' }}>
            <ErrorBoundary>
              <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
                <Route path="/chat" element={<ErrorBoundary><ChatLayout /></ErrorBoundary>} />
                <Route path="/tasks" element={<ErrorBoundary><Tasks /></ErrorBoundary>} />
                <Route path="/plans" element={<ErrorBoundary><Plans /></ErrorBoundary>} />
                <Route path="/memory" element={<ErrorBoundary><Memory /></ErrorBoundary>} />
                <Route path="/system" element={<ErrorBoundary><System /></ErrorBoundary>} />
              </Routes>
            </ErrorBoundary>
          </Layout.Content>
        </Layout>
      </Layout>
    </ErrorBoundary>
  );
}

export default App;
