import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { SystemStatus } from '@/types';

interface SystemState {
  // 系统状态
  systemStatus: SystemStatus;
  apiConnected: boolean;
  loading: boolean;
  
  // WebSocket连接状态
  wsConnected: boolean;
  wsReconnecting: boolean;
  
  // 操作方法
  setSystemStatus: (status: SystemStatus) => void;
  setApiConnected: (connected: boolean) => void;
  setLoading: (loading: boolean) => void;
  setWsConnected: (connected: boolean) => void;
  setWsReconnecting: (reconnecting: boolean) => void;
  
  // 系统统计
  incrementApiCalls: () => void;
  updateSystemLoad: (load: Partial<SystemStatus['system_load']>) => void;
}

export const useSystemStore = create<SystemState>()(
  subscribeWithSelector((set, get) => ({
    // 初始状态
    systemStatus: {
      api_connected: false,
      database_status: 'disconnected',
      active_tasks: 0,
      total_plans: 0,
      system_load: {
        cpu: 0,
        memory: 0,
        api_calls_per_minute: 0,
      },
    },
    apiConnected: false,
    loading: false,
    wsConnected: false,
    wsReconnecting: false,

    // 设置系统状态
    setSystemStatus: (status) => set({ systemStatus: status }),
    
    // 设置API连接状态
    setApiConnected: (connected) => set((state) => ({ 
      apiConnected: connected,
      systemStatus: { ...state.systemStatus, api_connected: connected }
    })),
    
    // 设置加载状态
    setLoading: (loading) => set({ loading }),
    
    // 设置WebSocket连接状态
    setWsConnected: (connected) => set({ wsConnected: connected }),
    
    // 设置WebSocket重连状态
    setWsReconnecting: (reconnecting) => set({ wsReconnecting: reconnecting }),
    
    // 增加API调用次数
    incrementApiCalls: () => set((state) => ({
      systemStatus: {
        ...state.systemStatus,
        system_load: {
          ...state.systemStatus.system_load,
          api_calls_per_minute: state.systemStatus.system_load.api_calls_per_minute + 1,
        },
      },
    })),
    
    // 更新系统负载
    updateSystemLoad: (load) => set((state) => ({
      systemStatus: {
        ...state.systemStatus,
        system_load: {
          ...state.systemStatus.system_load,
          ...load,
        },
      },
    })),
  }))
);
