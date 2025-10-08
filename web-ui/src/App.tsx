import { useEffect } from 'react';
import { message } from 'antd';
import ChatLayout from '@components/layout/ChatLayout';
import { useSystemStore } from '@store/system';
import { checkApiHealth } from '@api/client';

function App() {
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
          message.error('❌ 后端服务连接失败！请检查后端是否运行在 http://localhost:8000', 10);
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
  }, []); // 移除store函数依赖，避免无限循环

  return <ChatLayout />;
}

export default App;
