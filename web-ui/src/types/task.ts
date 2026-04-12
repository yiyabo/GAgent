import type { DecompositionJobStatus } from './tool';
import type { ContextOptions } from './settings';

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

export interface VerifyTaskResponse {
  success: boolean;
  message: string;
  plan_id: number;
  task_id: number;
  result: PlanResultItem;
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

export interface DependencyNodeInfo {
  id: number;
  name: string;
  status: string;
}

export interface ExecutionChecklistItem {
  step_index: number;
  task_id: number;
  name: string;
  status: string;
  execution_state: 'completed' | 'failed' | 'running' | 'blocked' | 'ready' | string;
  instruction?: string | null;
  depends_on: number[];
  unmet_dependencies: number[];
  expected_deliverables: string[];
  is_target: boolean;
}

export interface DependencyPlanResponse {
  plan_id: number;
  target_task_id: number;
  satisfied_statuses: string[];
  direct_dependencies: number[];
  closure_dependencies: number[];
  missing_dependencies: DependencyNodeInfo[];
  running_dependencies: DependencyNodeInfo[];
  execution_order: number[];
  execution_items: ExecutionChecklistItem[];
  cycle_detected: boolean;
  cycle_paths: number[][];
}

export interface ExecuteTaskResponse {
  success: boolean;
  message: string;
  plan_id: number;
  task_id: number;
  dependency_plan: DependencyPlanResponse;
  job?: DecompositionJobStatus | null;
  result?: Record<string, any> | null;
}

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

// ---- Todo-List (phased execution plan) ----

export interface TodoItemResponse {
  task_id: number;
  name: string;
  instruction: string | null;
  status: string;
  dependencies: number[];
  phase: number;
}

export interface TodoPhaseResponse {
  phase_id: number;
  label: string;
  status: string;
  total: number;
  completed: number;
  items: TodoItemResponse[];
}

export interface TodoListResponse {
  plan_id: number;
  target_task_id: number;
  total_tasks: number;
  completed_tasks: number;
  phases: TodoPhaseResponse[];
  execution_order: number[];
  pending_order: number[];
  summary: string;
}
