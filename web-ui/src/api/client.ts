import axios, { AxiosInstance, AxiosError, AxiosResponse } from 'axios';
import { ApiResponse } from '../types/index';
import { ENV } from '@/config/env';

// 创建axios实例
const createApiClient = (): AxiosInstance => {
  const client = axios.create({
    baseURL: ENV.API_BASE_URL,  // 从环境变量读取后端API地址
    timeout: 1200000,
    headers: {
      'Content-Type': 'application/json',
    },
  });

  // 请求拦截器
  client.interceptors.request.use(
    (config) => {
      // 记住用户要求：禁用Mock模式，使用真实API
      // 确保不发送任何Mock相关的headers
      console.log(`🚀 API Request: ${config.method?.toUpperCase()} ${config.url}`);
      return config;
    },
    (error) => {
      console.error('❌ Request Error:', error);
      return Promise.reject(error);
    }
  );

  // 响应拦截器
  client.interceptors.response.use(
    (response: AxiosResponse<ApiResponse>) => {
      console.log(`✅ API Response: ${response.status} ${response.config.url}`);
      return response;
    },
    (error: AxiosError<ApiResponse>) => {
      console.error('❌ Response Error:', error.response?.status, error.response?.data);
      
      // 统一错误处理
      const status = error.response?.status;
      if (status === 401) {
        // 处理认证错误
        console.error('Authentication required');
      } else if (status === 403) {
        // 处理权限错误
        console.error('Permission denied');
      } else if (status && status >= 500) {
        // 处理服务器错误
        console.error('Server error occurred');
      }
      
      return Promise.reject(error);
    }
  );

  return client;
};

export const apiClient = createApiClient();

// API 基础类
export class BaseApi {
  protected client: AxiosInstance;

  constructor() {
    this.client = apiClient;
  }

  protected async request<T>(
    method: 'get' | 'post' | 'put' | 'patch' | 'delete',
    url: string,
    data?: any,
    params?: Record<string, any>
  ): Promise<T> {
    try {
      const response = await this.client.request({
        method,
        url,
        data,
        params,
      });
      
      console.log(`📡 API ${method.toUpperCase()} ${url}:`, response.status, response.data);
      
      // 后端直接返回数据，不包装在ApiResponse中
      return response.data as T;
    } catch (error) {
      console.error(`❌ API ${method.toUpperCase()} ${url} failed:`, error);
      
      if (axios.isAxiosError(error)) {
        const errorData = error.response?.data;
        if (errorData?.error) {
          // 处理后端错误格式 { success: false, error: {...} }
          throw new Error(errorData.error.message || 'API request failed');
        } else {
          // 处理其他错误
          const message = errorData?.message || error.message || 'API request failed';
          throw new Error(message);
        }
      }
      throw error;
    }
  }

  protected async get<T>(url: string, params?: Record<string, any>): Promise<T> {
    return this.request<T>('get', url, undefined, params);
  }

  protected async post<T>(url: string, data?: any): Promise<T> {
    return this.request<T>('post', url, data);
  }

  protected async put<T>(url: string, data?: any): Promise<T> {
    return this.request<T>('put', url, data);
  }

  protected async patch<T>(url: string, data?: any): Promise<T> {
    return this.request<T>('patch', url, data);
  }

  protected async delete<T>(url: string, params?: Record<string, any>): Promise<T> {
    return this.request<T>('delete', url, undefined, params);
  }
}

// 健康检查函数
export const checkApiHealth = async (): Promise<{
  api_connected: boolean;
  llm_status: any;
}> => {
  console.log('🔍 Starting health check...');
  
  try {
    // 直接调用后端API，绕过代理问题
    console.log('🌐 Checking health endpoint...');
    const healthResponse = await fetch(`${ENV.API_BASE_URL}/health`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });
    
    console.log('📡 Health response status:', healthResponse.status);
    
    if (healthResponse.ok) {
      const healthData = await healthResponse.json();
      console.log('✅ Health data:', healthData);
      
      console.log('🧠 Checking LLM endpoint...');
      const llmResponse = await fetch(`${ENV.API_BASE_URL}/health/llm?ping=true`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });
      
      console.log('🤖 LLM response status:', llmResponse.status);
      
      if (llmResponse.ok) {
        const llmData = await llmResponse.json();
        console.log('🚀 LLM data:', llmData);
        
        return {
          api_connected: true,
          llm_status: llmData,
        };
      } else {
        console.warn('⚠️ LLM endpoint failed, but API is accessible');
        return {
          api_connected: true, // API基本可用
          llm_status: null,
        };
      }
    } else {
      console.error('❌ Health endpoint failed:', healthResponse.status, healthResponse.statusText);
      return {
        api_connected: false,
        llm_status: null,
      };
    }
  } catch (error) {
    console.error('❌ Health check exception:', error);
    
    // 检查是否是CORS错误
    if (error instanceof TypeError && error.message.includes('fetch')) {
      console.error('🚫 Likely CORS issue - cannot reach backend');
    }
    
    return {
      api_connected: false,
      llm_status: null,
    };
  }
};
