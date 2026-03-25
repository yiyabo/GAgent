import axios, { AxiosInstance, AxiosError, AxiosResponse } from 'axios';
import { ApiResponse } from '../types/index';
import { ENV } from '@/config/env';
import { emitAuthUnauthorized } from '@/auth/events';

export interface AuthAwareRequestConfig {
  skipAuthHandling?: boolean;
}

const createApiClient = (): AxiosInstance => {
  const client = axios.create({
  baseURL: ENV.API_BASE_URL,
  // Default timeout for general API requests (session CRUD, health checks, etc.).
  // LLM-heavy endpoints (chat/message, chat/actions) override this per-request.
  timeout: 30000,
  headers: {
  'Content-Type': 'application/json',
  },
  withCredentials: true,
  });

  // ---- Timeout tier interceptor ----
  // Automatically escalate timeouts for known slow paths so callers don't
  // need to remember to set them manually.
  const TIMEOUT_TIERS: Array<{ pattern: RegExp; ms: number }> = [
    // Chat message (synchronous LLM round-trip): 2 minutes
    { pattern: /\/chat\/message$/, ms: 120_000 },
    // Chat runs (create run + background execution): 2 minutes
    { pattern: /\/chat\/runs/, ms: 120_000 },
    // Action status polling & retry: 5 minutes
    { pattern: /\/chat\/actions\//, ms: 300_000 },
    // Session autotitle (LLM call): 60 seconds
    { pattern: /\/autotitle/, ms: 60_000 },
    // Task execution endpoints (LLM + tool execution): 5 minutes
    { pattern: /\/run$/, ms: 300_000 },
    { pattern: /\/execute/, ms: 300_000 },
    { pattern: /\/rerun$/, ms: 300_000 },
    // Task verification (LLM call): 2 minutes
    { pattern: /\/verify/, ms: 120_000 },
    // Plan decomposition / evaluation (multi-step LLM): 3 minutes
    { pattern: /\/decompos/, ms: 180_000 },
    { pattern: /\/evaluat/, ms: 180_000 },
    // Plan proposal / approval (LLM): 2 minutes
    { pattern: /\/plans\/propose/, ms: 120_000 },
    { pattern: /\/plans\/approve/, ms: 120_000 },
  ];

  client.interceptors.request.use(
  (config) => {
  // Apply timeout tier unless the caller already set a custom timeout.
  if (config.timeout === 30000 && config.url) {
    for (const tier of TIMEOUT_TIERS) {
      if (tier.pattern.test(config.url)) {
        config.timeout = tier.ms;
        break;
      }
    }
  }
  console.log(`🚀 API Request: ${config.method?.toUpperCase()} ${config.url}`);
  return config;
  },
  (error) => {
  console.error('❌ Request Error:', error);
  return Promise.reject(error);
  }
  );

  client.interceptors.response.use(
  (response: AxiosResponse<ApiResponse>) => {
  console.log(`✅ API Response: ${response.status} ${response.config.url}`);
  return response;
  },
  (error: AxiosError<ApiResponse>) => {
  console.error('❌ Response Error:', error.response?.status, error.response?.data);
  
  const status = error.response?.status;
  const skipAuthHandling = Boolean((error.config as AxiosError<ApiResponse>['config'] & AuthAwareRequestConfig | undefined)?.skipAuthHandling);
  if (status === 401 && !skipAuthHandling) {
  console.error('Authentication required');
  emitAuthUnauthorized();
  } else if (status === 403) {
  console.error('Permission denied');
  } else if (status && status >= 500) {
  console.error('Server error occurred');
  }
  
  return Promise.reject(error);
  }
  );

  return client;
};

export const apiClient = createApiClient();

export class BaseApi {
  protected client: AxiosInstance;

  constructor() {
  this.client = apiClient;
  }

  protected async request<T>(
  method: 'get' | 'post' | 'put' | 'patch' | 'delete',
  url: string,
  data?: any,
  params?: Record<string, any>,
  options?: { signal?: AbortSignal },
  ): Promise<T> {
  try {
  const response = await this.client.request({
  method,
  url,
  data,
  params,
  signal: options?.signal,
  });
  
  console.log(`📡 API ${method.toUpperCase()} ${url}:`, response.status, response.data);
  
  return response.data as T;
  } catch (error) {
  console.error(`❌ API ${method.toUpperCase()} ${url} failed:`, error);
  
  if (axios.isAxiosError(error)) {
  const errorData = error.response?.data;
  if (errorData?.error) {
  throw new Error(errorData.error.message || 'API request failed');
  } else {
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

export const checkApiHealth = async (): Promise<{
  api_connected: boolean;
  llm_status: any;
}> => {
  console.log('🔍 Starting health check...');
  
  try {
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
  api_connected: true, // APIavailable
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
  
  if (error instanceof TypeError && error.message.includes('fetch')) {
  console.error('🚫 Likely CORS issue - cannot reach backend');
  }
  
  return {
  api_connected: false,
  llm_status: null,
  };
  }
};
