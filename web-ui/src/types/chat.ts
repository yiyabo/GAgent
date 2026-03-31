import type { ChatSessionSettings, WebSearchProvider, BaseModelOption, LLMProviderOption } from './settings';
import type { ToolResultPayload, DecompositionJobStatus, JobLogEvent } from './tool';

export interface ChatSessionSummary {
  id: string;
  name?: string | null;
  name_source?: string | null;
  is_user_named?: boolean | null;
  plan_id?: number | null;
  plan_title?: string | null;
  current_task_id?: number | null;
  current_task_name?: string | null;
  last_message_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  is_active: boolean;
  settings?: ChatSessionSettings | null;
}

export interface ChatSessionsResponse {
  sessions: ChatSessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

export type ChatActionStatus = 'pending' | 'running' | 'completed' | 'failed';
export type RequestTier = 'light' | 'standard' | 'research' | 'execute';
export type RequestRouteMode = 'manual_deepthink' | 'auto_simple' | 'auto_deepthink';
export type ThinkingVisibility = 'visible' | 'progress' | 'hidden';

export interface CompactProgressToolItem {
  tool: string;
  label?: string | null;
  status: 'running' | 'retrying' | 'failed' | 'completed';
  details?: string | null;
}

export interface CompactProgressHistoryItem {
  phase?: string | null;
  label?: string | null;
  tool?: string | null;
  status?: string | null;
}

export interface CompactProgressState {
  phase?: string | null;
  label?: string | null;
  iteration?: number | null;
  tool?: string | null;
  status?: string | null;
  current_tool?: string | null;
  current_label?: string | null;
  current_status?: string | null;
  current_details?: string | null;
  tool_items?: CompactProgressToolItem[];
  expanded_notes?: string[];
  history?: CompactProgressHistoryItem[];
  updated_at?: string | null;
}

export interface ChatActionSummary {
  kind?: string | null;
  name?: string | null;
  parameters?: Record<string, any> | null;
  order?: number | null;
  blocking?: boolean | null;
  status?: ChatActionStatus | null;
  success?: boolean | null;
  message?: string | null;
  details?: Record<string, any> | null;
}

export type PlanSyncEventType =
  | 'plan_created'
  | 'plan_deleted'
  | 'plan_updated'
  | 'task_changed'
  | 'plan_jobs_completed'
  | 'session_deleted'
  | 'session_archived';

export interface PlanSyncEventDetail {
  type: PlanSyncEventType;
  plan_id: number | null;
  plan_title?: string | null;
  job_id?: string | null;
  job_type?: string | null;
  status?: string | null;
  session_id?: string | null;
  tracking_id?: string | null;
  source?: string | null;
  triggered_at?: string;
  raw?: unknown;
}

export interface ArtifactGalleryItem {
  path: string;
  display_name?: string | null;
  source_tool?: string | null;
  mime_family?: string | null;
  origin?: string | null;
  created_at?: string | null;
  tracking_id?: string | null;
}

export interface ChatResponseMetadata {
  status?: ChatActionStatus;
  tracking_id?: string;
  plan_id?: number | null;
  plan_title?: string | null;
  plan_creation_state?: 'created' | 'updated' | 'text_only' | 'failed' | null;
  plan_creation_message?: string | null;
  plan_outline?: string | null;
  plan_persisted?: boolean;
  success?: boolean;
  errors?: string[];
  raw_actions?: any[];
  agent_workflow?: boolean;
  total_tasks?: number;
  dag_structure?: any;
  finished_at?: string | null;
  workflow_id?: string | null;
  task_id?: number | null;
  task_name?: string | null;
  session_id?: string;
  tool_results?: ToolResultPayload[] | null;
  artifact_gallery?: ArtifactGalleryItem[] | null;
  request_tier?: RequestTier;
  request_route_mode?: RequestRouteMode;
  route_reason_codes?: string[];
  thinking_visibility?: ThinkingVisibility;
  deep_think_progress?: CompactProgressState;
  [key: string]: any;
}

export interface ChatResponsePayload {
  response: string;
  suggestions?: string[];
  actions?: ChatActionSummary[];
  metadata?: ChatResponseMetadata;
}

export interface ActionStatusResponse {
  tracking_id: string;
  status: ChatActionStatus;
  plan_id?: number | null;
  actions?: ChatActionSummary[];
  result?: Record<string, any> | null;
  errors?: string[];
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  metadata?: {
    tool_results?: ToolResultPayload[] | null;
    artifact_gallery?: ArtifactGalleryItem[] | null;
    final_summary?: string;
    [key: string]: any;
  } | null;
}

export interface ChatMessage {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  metadata?: {
    task_id?: number;
    plan_title?: string | null;
    task_name?: string | null;
    workflow_id?: string | null;
    session_id?: string;
    backend_id?: number;
    plan_id?: number | null;
    plan_creation_state?: 'created' | 'updated' | 'text_only' | 'failed' | null;
    plan_creation_message?: string | null;
    status?: ChatActionStatus;
    tracking_id?: string;
    errors?: string[];
    raw_actions?: any[];
    actions?: ChatActionSummary[];
    action_list?: ChatActionSummary[];
    actions_summary?: Array<{
      order?: number | null;
      kind?: string | null;
      name?: string | null;
      success?: boolean | null;
      message?: string | null;
    }>;
    code_blocks?: string[];
    attachments?: Array<{
      type: 'file' | 'image';
      path: string;
      name: string;
      extracted_path?: string;
    }>;
    task_search_result?: boolean;
    tasks_found?: number;
    tool_executed?: boolean;
    tool_type?: string;
    tool_results?: ToolResultPayload[] | null;
    artifact_gallery?: ArtifactGalleryItem[] | null;
    request_tier?: RequestTier;
    request_route_mode?: RequestRouteMode;
    route_reason_codes?: string[];
    thinking_visibility?: ThinkingVisibility;
    deep_think_progress?: CompactProgressState;
    type?: string;
    job?: DecompositionJobStatus | null;
    job_id?: string;
    job_status?: string;
    job_logs?: JobLogEvent[];
    [key: string]: any;
  };
  thinking_process?: ThinkingProcess;
}

export interface ThinkingStep {
  iteration: number;
  thought: string;
  display_text?: string;
  kind?: 'reasoning' | 'tool' | 'summary';
  action?: string | null;
  action_result?: string | null;
  evidence?: ThinkingEvidenceItem[];
  status: 'pending' | 'thinking' | 'calling_tool' | 'analyzing' | 'done' | 'completed' | 'error';
  timestamp?: string;
  self_correction?: string | null;
  started_at?: string;
  finished_at?: string;
}

export interface ThinkingEvidenceItem {
  type: string;
  title?: string;
  ref?: string;
  snippet?: string;
}

export interface ThinkingProcess {
  steps: ThinkingStep[];
  status: 'active' | 'completed' | 'error';
  summary?: string | null;
  total_iterations?: number;
  error?: string | null;
}

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  created_at: Date;
  updated_at: Date;
  workflow_id?: string | null;
  session_id?: string | null;
  plan_id?: number | null;
  plan_title?: string | null;
  current_task_id?: number | null;
  current_task_name?: string | null;
  last_message_at?: Date | null;
  is_active?: boolean;
  defaultSearchProvider?: WebSearchProvider | null;
  defaultBaseModel?: BaseModelOption | null;
  defaultLLMProvider?: LLMProviderOption | null;
  titleSource?: string | null;
  isUserNamed?: boolean | null;
}

export interface ChatSessionUpdatePayload {
  name?: string | null;
  is_active?: boolean | null;
  plan_id?: number | null;
  plan_title?: string | null;
  current_task_id?: number | null;
  current_task_name?: string | null;
  settings?: ChatSessionSettings | null;
}

export interface ChatSessionAutoTitleResult {
  session_id: string;
  title: string;
  source: string;
  updated: boolean;
  previous_title?: string | null;
  skipped_reason?: string | null;
}

export interface ChatSessionAutoTitleBulkResponse {
  results: ChatSessionAutoTitleResult[];
  processed: number;
}

export interface WSMessage {
  type: 'task_update' | 'chat_message' | 'system_status' | 'execution_progress';
  data: any;
  timestamp: Date;
}
