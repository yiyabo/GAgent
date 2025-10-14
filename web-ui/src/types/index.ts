// 任务相关类型定义
export interface Task {
  id: number;
  name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  priority?: number;
  parent_id?: number;
  path?: string;
  depth?: number;
  task_type?: 'root' | 'composite' | 'atomic';
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
export interface ChatMessage {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  metadata?: {
    task_id?: number;
    plan_title?: string;
    task_name?: string;
    workflow_id?: string;
    session_id?: string;
    code_blocks?: string[];
    attachments?: string[];
    actions?: Array<{
      type: string;
      data: any;
    }>;
    // 任务搜索相关
    task_search_result?: boolean;
    tasks_found?: number;
    // 工具执行相关
    tool_executed?: boolean;
    tool_type?: string;
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
