// 任务相关类型定义
export interface Task {
  id: number;
  name: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  parent_id?: number;
  path?: string;
  depth?: number;
  task_type?: 'root' | 'composite' | 'atomic';
  plan_id?: number;
  instruction?: string | null;
  dependencies?: number[];
  position?: number;
  metadata?: Record<string, any>;
  context_combined?: string | null;
  context_sections?: Array<Record<string, any>>;
  context_meta?: Record<string, any>;
  context_updated_at?: string | null;
  execution_result?: string | null;
  created_at?: string;
  updated_at?: string;
  session_id?: string | null;
  workflow_id?: string | null;
  root_id?: number | null;
}

export interface TaskInput {
  task_id: number;
  prompt: string;
}

export interface TaskOutput {
  task_id: number;
  content: string;
}

// 计划相关类型
export interface Plan {
  title: string;
  tasks: Task[];
  created_at: string;
  status: 'draft' | 'approved' | 'executing' | 'completed';
}

export interface PlanTaskNode extends Task {
  short_name?: string;
}

export interface PlanProposal {
  goal: string;
  title?: string;
  sections?: number;
  style?: string;
  notes?: string;
}

export interface PlanSummary {
  id: number;
  title: string;
  description?: string | null;
  task_count: number;
  updated_at?: string | null;
  metadata?: Record<string, any>;
}

export interface PlanNodeResponse {
  id: number;
  plan_id: number;
  name: string;
  status?: string;
  instruction?: string | null;
  parent_id?: number | null;
  position?: number;
  depth?: number;
  path?: string;
  metadata?: Record<string, any>;
  dependencies?: number[];
  context_combined?: string | null;
  context_sections?: Array<Record<string, any>>;
  context_meta?: Record<string, any>;
  context_updated_at?: string | null;
  execution_result?: string | null;
}

export interface PlanTreeResponse {
  id: number;
  title: string;
  description?: string | null;
  metadata?: Record<string, any>;
  nodes: Record<string, PlanNodeResponse>;
  adjacency: Record<string, number[]>;
}

export interface PlanResultItem {
  task_id: number;
  name?: string | null;
  status?: Task['status'] | string | null;
  content?: string | null;
  notes?: string[];
  metadata?: Record<string, any>;
  raw?: Record<string, any> | null;
}

export interface PlanResultsResponse {
  plan_id: number;
  total: number;
  items: PlanResultItem[];
}

export interface PlanExecutionSummary {
  plan_id: number;
  total_tasks: number;
  completed: number;
  failed: number;
  skipped: number;
  running: number;
  pending: number;
}

export type WebSearchProvider = 'builtin' | 'perplexity';

export interface ChatSessionSettings {
  default_search_provider?: WebSearchProvider | null;
}

// 聊天会话摘要（来自后端）
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

// 评估相关类型
export interface EvaluationDimensions {
  relevance: number;
  completeness: number;
  accuracy: number;
  clarity: number;
  coherence: number;
  scientific_rigor: number;
}

export interface EvaluationResult {
  overall_score: number;
  dimensions: EvaluationDimensions;
  suggestions: string[];
  needs_revision: boolean;
  iteration: number;
  timestamp?: string;
  metadata?: Record<string, any>;
}

// DAG可视化相关类型
export interface DAGNode {
  id: string;
  label: string;
  group: 'root' | 'composite' | 'atomic';
  status: Task['status'];
  level: number;
  x?: number;
  y?: number;
}

export interface DAGEdge {
  from: string;
  to: string;
  label?: string;
  color?: string;
  dashes?: boolean;
}

// 聊天相关类型
export type ChatActionStatus = 'pending' | 'running' | 'completed' | 'failed';

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


export interface ToolResultItem {
  title?: string | null;
  url?: string | null;
  snippet?: string | null;
  source?: string | null;
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

export interface ChatResponseMetadata {
  status?: ChatActionStatus;
  tracking_id?: string;
  plan_id?: number | null;
  plan_title?: string | null;
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
    plan_id?: number | null;
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
    attachments?: string[];
    task_search_result?: boolean;
    tasks_found?: number;
    tool_executed?: boolean;
    tool_type?: string;
    tool_results?: ToolResultPayload[] | null;
    type?: string;
    job?: DecompositionJobStatus | null;
    job_id?: string;
    job_status?: string;
    job_logs?: JobLogEvent[];
    [key: string]: any;
  };
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

// WebSocket 消息类型
export interface WSMessage {
  type: 'task_update' | 'chat_message' | 'system_status' | 'execution_progress';
  data: any;
  timestamp: Date;
}

// API 响应类型
export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  message?: string;
  error?: string;
}

// 系统状态类型
export interface SystemStatus {
  api_connected: boolean;
  database_status: 'connected' | 'disconnected' | 'error';
  active_tasks: number;
  total_plans: number;
  system_load: {
    cpu: number;
    memory: number;
    api_calls_per_minute: number;
  };
}

export interface ChatStatusResponse {
  status: string;
  llm: {
    provider?: string | null;
    model?: string | null;
    api_url?: string | null;
    has_api_key: boolean;
    mock_mode: boolean;
  };
  decomposer: {
    provider?: string | null;
    model?: string | null;
    auto_on_create: boolean;
    max_depth: number;
    total_node_budget: number;
  };
  executor: {
    provider?: string | null;
    model?: string | null;
    serial: boolean;
    use_context: boolean;
    max_tasks?: number | null;
  };
  features: Record<string, any>;
  warnings: string[];
}

// 工具调用相关类型
export interface ToolCall {
  tool_name: string;
  parameters: Record<string, any>;
  result?: any;
  status: 'pending' | 'running' | 'completed' | 'failed';
  timestamp: Date;
  cost?: number;
}

// 上下文相关类型
export interface ContextSection {
  task_id: number;
  name: string;
  short_name: string;
  kind: string;
  content: string;
}

export interface ContextOptions {
  include_deps: boolean;
  include_plan: boolean;
  k: number;
  manual?: number[];
  semantic_k: number;
  min_similarity: number;
  include_ancestors: boolean;
  max_chars: number;
  strategy: 'sentence' | 'truncate';
}

// 执行相关类型
export interface ExecutionRequest {
  title?: string;
  target_task_id?: number;
  schedule: 'bfs' | 'dag' | 'postorder';
  use_context: boolean;
  enable_evaluation: boolean;
  evaluation_mode?: 'llm' | 'multi_expert' | 'adversarial';
  use_tools: boolean;
  auto_decompose: boolean;
  context_options?: ContextOptions;
}

export interface ExecutionResult {
  id: number;
  status: string;
  evaluation_mode?: string;
  evaluation?: {
    score: number;
    iterations: number;
  };
  artifacts?: string[];
}

// Memory-MCP 相关类型
export interface Memory {
  id: string;
  content: string;
  memory_type: 'conversation' | 'experience' | 'knowledge' | 'context';
  importance: 'critical' | 'high' | 'medium' | 'low' | 'temporary';
  keywords: string[];
  tags: string[];
  context: string;
  related_task_id?: number;
  created_at: string;
  last_accessed?: string;
  retrieval_count: number;
  similarity?: number;
  links?: MemoryLink[];
}

export interface MemoryLink {
  memory_id: string;
  similarity: number;
}

export interface MemoryStats {
  total_memories: number;
  memory_type_distribution: Record<string, number>;
  importance_distribution: Record<string, number>;
  average_connections: number;
  embedding_coverage: number;
  evolution_count?: number;
}

export interface SaveMemoryRequest {
  content: string;
  memory_type: Memory['memory_type'];
  importance: Memory['importance'];
  tags?: string[];
  keywords?: string[];
  context?: string;
  related_task_id?: number;
}

export interface QueryMemoryRequest {
  search_text: string;
  memory_types?: Memory['memory_type'][];
  importance_levels?: Memory['importance'][];
  limit?: number;
  min_similarity?: number;
}

export interface QueryMemoryResponse {
  memories: Memory[];
  total: number;
  search_time_ms: number;
}
