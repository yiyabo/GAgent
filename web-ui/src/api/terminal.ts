import { BaseApi } from './client';
import { ENV } from '@/config/env';
import type {
  TerminalAuditEntry,
  TerminalCreateRequest,
  TerminalReplayEvent,
  TerminalSessionInfo,
} from '@/types';

class TerminalApi extends BaseApi {
  listSessions = async (sessionId?: string): Promise<TerminalSessionInfo[]> => {
    return this.get<TerminalSessionInfo[]>('/api/v1/terminal/sessions', {
      session_id: sessionId,
    });
  };

  createSession = async (payload: TerminalCreateRequest): Promise<TerminalSessionInfo> => {
    return this.post<TerminalSessionInfo>('/api/v1/terminal/sessions', payload);
  };

  closeSession = async (terminalId: string): Promise<{ success: boolean; terminal_id: string }> => {
    return this.delete<{ success: boolean; terminal_id: string }>(`/api/v1/terminal/sessions/${terminalId}`);
  };

  getReplay = async (terminalId: string, limit = 4000): Promise<TerminalReplayEvent[]> => {
    return this.get<TerminalReplayEvent[]>(`/api/v1/terminal/sessions/${terminalId}/replay`, { limit });
  };

  getAudit = async (
    terminalId: string,
    options?: {
      startTs?: number;
      endTs?: number;
      eventType?: string;
      limit?: number;
    }
  ): Promise<TerminalAuditEntry[]> => {
    return this.get<TerminalAuditEntry[]>('/api/v1/terminal/audit', {
      terminal_id: terminalId,
      start_ts: options?.startTs,
      end_ts: options?.endTs,
      event_type: options?.eventType,
      limit: options?.limit,
    });
  };
}

export const terminalApi = new TerminalApi();

export const buildTerminalWsUrl = (
  sessionId: string,
  options?: { mode?: 'sandbox' | 'ssh'; terminalId?: string }
): string => {
  const base = ENV.WS_BASE_URL.replace(/\/$/, '');
  const params = new URLSearchParams();
  if (options?.mode) params.set('mode', options.mode);
  if (options?.terminalId) params.set('terminal_id', options.terminalId);
  const query = params.toString();
  return `${base}/ws/terminal/${encodeURIComponent(sessionId)}${query ? `?${query}` : ''}`;
};
