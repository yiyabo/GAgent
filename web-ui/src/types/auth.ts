export interface AuthUser {
  user_id: string;
  email: string;
  role: string;
  auth_source: string;
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
