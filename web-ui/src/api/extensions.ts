import axios from 'axios';
import { BaseApi } from './client';

// ---- Contract v1: /extensions/mcp -------------------------------------------------

export type McpIsolation = 'container' | 'host' | 'remote';
export type McpState = 'enabled' | 'disabled' | 'error' | 'unavailable';
export type McpCategory = 'bioinformatics' | 'web' | 'utility' | 'reasoning' | string;

export interface McpVerificationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface McpVerification {
  status: 'verified' | 'unverified';
  reviewed_at: string;
  reviewer: string;
  human_signoff: boolean;
  checks: McpVerificationCheck[];
}

export interface McpSource {
  repo: string;
  commit: string;
  package: string;
}

export interface McpToolDecl {
  name: string;
  read_only: boolean;
}

export interface McpServer {
  id: string;
  title: string;
  description: string;
  category: McpCategory;
  homepage: string;
  license: string;
  version: string;
  source: McpSource;
  verification: McpVerification;
  isolation: McpIsolation;
  egress: string[];
  tools: McpToolDecl[];
  state: McpState;
  connected: boolean;
  error: string | null;
  live_tools: string[];
  unavailable_reason: string | null;
}

export interface McpCustomEntry {
  name: string;
  transport: 'stdio' | 'http';
  isolation: McpIsolation;
  connected: boolean;
  error: string | null;
  tools: string[];
}

export interface McpListResponse {
  configured: boolean;
  can_manage: boolean;
  servers: McpServer[];
  custom: McpCustomEntry[];
}

// ---- Contract v1: /extensions/skills ----------------------------------------------

export type SkillScanner = 'passed' | 'warnings' | 'blocked' | 'n/a';
export type SkillFindingSeverity = 'block' | 'warn' | 'info';

export interface SkillOrigin {
  kind: 'internal' | 'third_party';
  source: string;
  license: string;
  commit: string | null;
  modified: boolean;
  modifications: string[];
}

export interface SkillReview {
  scanner: SkillScanner;
  reviewed_by: string;
  reviewed_at: string;
  human_signoff: boolean;
  notes: string;
}

export interface SkillFinding {
  severity: SkillFindingSeverity;
  rule: string;
  file: string;
  line: number | null;
  detail: string;
}

export interface SkillEntry {
  name: string;
  description: string;
  origin: SkillOrigin;
  review: SkillReview;
  findings: SkillFinding[];
  enabled: boolean;
  size_chars: number;
  scripts: number;
  references: number;
}

export interface SkillListResponse {
  can_manage: boolean;
  skills: SkillEntry[];
}

/** Error carrying the backend `detail` text and the HTTP status. */
export class ExtensionsApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ExtensionsApiError';
    this.status = status;
  }
}

function extractDetail(data: unknown): string | null {
  if (!data || typeof data !== 'object') return null;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (item && typeof item === 'object' ? String((item as { msg?: unknown }).msg ?? '') : String(item)))
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  return null;
}

export class ExtensionsApi extends BaseApi {
  // The shared BaseApi error path drops FastAPI's `{detail}` body, which this
  // page must show verbatim (403/404/409 reasons), so requests go through here.
  protected async request<T>(
    method: 'get' | 'post' | 'put' | 'patch' | 'delete',
    url: string,
    data?: any,
    params?: Record<string, any>,
    options?: { signal?: AbortSignal },
  ): Promise<T> {
    try {
      const response = await this.client.request({ method, url, data, params, signal: options?.signal });
      return response.data as T;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        const detail = extractDetail(error.response?.data);
        throw new ExtensionsApiError(detail || error.message || 'API request failed', error.response?.status);
      }
      throw error;
    }
  }

  listMcp(): Promise<McpListResponse> {
    return this.get<McpListResponse>('/extensions/mcp');
  }

  enableMcp(id: string): Promise<McpServer> {
    return this.post<McpServer>(`/extensions/mcp/${encodeURIComponent(id)}/enable`);
  }

  disableMcp(id: string): Promise<McpServer> {
    return this.post<McpServer>(`/extensions/mcp/${encodeURIComponent(id)}/disable`);
  }

  listSkills(): Promise<SkillListResponse> {
    return this.get<SkillListResponse>('/extensions/skills');
  }

  enableSkill(name: string): Promise<SkillEntry> {
    return this.post<SkillEntry>(`/extensions/skills/${encodeURIComponent(name)}/enable`);
  }

  disableSkill(name: string): Promise<SkillEntry> {
    return this.post<SkillEntry>(`/extensions/skills/${encodeURIComponent(name)}/disable`);
  }
}

export const extensionsApi = new ExtensionsApi();
