import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { SystemStatus } from '@/types';

interface SystemState {
  systemStatus: SystemStatus;
  apiConnected: boolean;
  loading: boolean;
  
  wsConnected: boolean;
  wsReconnecting: boolean;
  
  setSystemStatus: (status: SystemStatus) => void;
  setApiConnected: (connected: boolean) => void;
  setLoading: (loading: boolean) => void;
  setWsConnected: (connected: boolean) => void;
  setWsReconnecting: (reconnecting: boolean) => void;
  
  incrementApiCalls: () => void;
  updateSystemLoad: (load: Partial<SystemStatus['system_load']>) => void;
}

export const useSystemStore = create<SystemState>()(
  subscribeWithSelector((set, get) => ({
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

    setSystemStatus: (status) => set({ systemStatus: status }),
    
    setApiConnected: (connected) => set((state) => ({ 
      apiConnected: connected,
      systemStatus: { ...state.systemStatus, api_connected: connected }
    })),
    
    setLoading: (loading) => set({ loading }),
    
    setWsConnected: (connected) => set({ wsConnected: connected }),
    
    setWsReconnecting: (reconnecting) => set({ wsReconnecting: reconnecting }),
    
    incrementApiCalls: () => set((state) => ({
      systemStatus: {
        ...state.systemStatus,
        system_load: {
          ...state.systemStatus.system_load,
          api_calls_per_minute: state.systemStatus.system_load.api_calls_per_minute + 1,
        },
      },
    })),
    
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
