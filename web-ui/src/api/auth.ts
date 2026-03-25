import { apiClient } from './client';
import type { AuthMeResponse, AuthSessionResponse } from '@/types/auth';

interface AuthPayload {
  email: string;
  password: string;
}

interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
}

const AUTH_REQUEST_OPTIONS = { skipAuthHandling: true } as any;

export const authApi = {
  async me(): Promise<AuthMeResponse> {
    const response = await apiClient.get<AuthMeResponse>('/auth/me', AUTH_REQUEST_OPTIONS);
    return response.data as AuthMeResponse;
  },

  async register(payload: AuthPayload): Promise<AuthSessionResponse> {
    const response = await apiClient.post<AuthSessionResponse>('/auth/register', payload, AUTH_REQUEST_OPTIONS);
    return response.data as AuthSessionResponse;
  },

  async login(payload: AuthPayload): Promise<AuthSessionResponse> {
    const response = await apiClient.post<AuthSessionResponse>('/auth/login', payload, AUTH_REQUEST_OPTIONS);
    return response.data as AuthSessionResponse;
  },

  async logout(): Promise<void> {
    await apiClient.post('/auth/logout', undefined, AUTH_REQUEST_OPTIONS);
  },

  async changePassword(payload: ChangePasswordPayload): Promise<AuthSessionResponse> {
    const response = await apiClient.post<AuthSessionResponse>('/auth/change-password', payload, AUTH_REQUEST_OPTIONS);
    return response.data as AuthSessionResponse;
  },
};
