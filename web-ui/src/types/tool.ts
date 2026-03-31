export interface ToolResultItem {
  title?: string | null;
  url?: string | null;
  snippet?: string | null;
  source?: string | null;
}

export interface ToolResultPayload {
  name?: string | null;
  summary?: string | null;
  parameters?: Record<string, any> | null;
  result?: {
    query?: string;
    response?: string;
    answer?: string;
    results?: ToolResultItem[] | null;
    error?: string;
    success?: boolean;
    search_engine?: string;
    total_results?: number;
    [key: string]: any;
  } | null;
}

export interface ToolCall {
  tool_name: string;
  parameters: Record<string, any>;
  result?: any;
  status: 'pending' | 'running' | 'completed' | 'failed';
  timestamp: Date;
  cost?: number;
}

export interface JobLogEvent {
  timestamp?: string | null;
  level: string;
  message: string;
  metadata?: Record<string, any>;
}

export interface ActionLogEntry {
  id?: number;
  job_id?: string;
  job_type?: string | null;
  plan_id?: number | null;
  sequence: number;
  action_kind: string;
  action_name: string;
  status: string;
  success?: boolean | null;
  message?: string | null;
  details?: Record<string, any> | null;
  session_id?: string | null;
  user_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DecompositionJobStatus {
  job_id: string;
  status: string;
  plan_id?: number | null;
  task_id?: number | null;
  mode?: string | null;
  job_type?: string | null;
  result?: Record<string, any> | null;
  stats?: Record<string, any> | null;
  params?: Record<string, any> | null;
  metadata?: Record<string, any> | null;
  error?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  logs?: JobLogEvent[];
  action_logs?: ActionLogEntry[];
  action_cursor?: string | null;
}

export interface JobLogTailResponse {
  job_id: string;
  log_path: string;
  total_lines: number;
  lines: string[];
  truncated: boolean;
}

export type BackgroundTaskCategory = 'task_creation' | 'phagescope' | 'code_executor';

export interface BackgroundTaskItem {
  category: BackgroundTaskCategory;
  job_id: string;
  job_type: string;
  status: string;
  label: string;
  session_id?: string | null;
  plan_id?: number | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  taskid?: string | null;
  remote_status?: string | null;
  phase?: string | null;
  progress_percent?: number | null;
  progress_status?: string | null;
  progress_text?: string | null;
  current_step?: number | null;
  total_steps?: number | null;
  done_steps?: number | null;
  current_task_id?: number | null;
  counts?: {
    done: number;
    total: number;
  } | null;
  error?: string | null;
}

export interface BackgroundTaskGroup {
  key: BackgroundTaskCategory;
  label: string;
  total: number;
  running: number;
  queued: number;
  succeeded: number;
  failed: number;
  items: BackgroundTaskItem[];
}

export interface BackgroundTaskBoardResponse {
  generated_at: string;
  total: number;
  groups: Record<BackgroundTaskCategory, BackgroundTaskGroup>;
}
