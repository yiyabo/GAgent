export type TerminalMode = 'sandbox' | 'ssh' | 'qwen_code';

export interface TerminalSessionInfo {
  terminal_id: string;
  session_id: string;
  mode: TerminalMode;
  state: 'creating' | 'active' | 'idle' | 'closing' | 'closed' | string;
  cwd: string;
  created_at: string;
  last_activity: string;
  pending_approvals: number;
}

export interface TerminalCreateRequest {
  session_id: string;
  mode?: TerminalMode;
  ssh_config?: {
    host: string;
    user: string;
    port?: number;
    ssh_key_path?: string;
    password?: string;
    connect_timeout?: number;
  };
}

export type TerminalWSMessageType =
  | 'input'
  | 'resize'
  | 'ping'
  | 'cmd_approve'
  | 'cmd_reject'
  | 'output'
  | 'approval'
  | 'closed'
  | 'error'
  | 'pong';

export interface TerminalWSMessage {
  type: TerminalWSMessageType;
  payload?: any;
  timestamp?: number;
}

export interface TerminalApprovalPayload {
  approval_id: string;
  command: string;
  risk_level: string;
  reason: string;
}

export interface TerminalReplayEvent {
  delay: number;
  type: 'i' | 'o';
  data: string;
}

export interface TerminalAuditEntry {
  id: number;
  timestamp: number;
  event_type: string;
  data: string;
  metadata: Record<string, any>;
}
