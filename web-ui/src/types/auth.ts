export interface PlatformContext {
  user_id: number;
  project_id: number | null;
  project_label: string | null;
}

export interface AuthUser {
  user_id: string;
  email: string;
  role: string;
  auth_source: string;
  access_mode: 'local' | 'platform';
  platform_context?: PlatformContext | null;
}

export interface AuthMeResponse {
  authenticated: boolean;
  user: AuthUser | null;
  legacy_access_allowed: boolean;
}

export interface AuthSessionResponse {
  authenticated: boolean;
  user: AuthUser;
}
