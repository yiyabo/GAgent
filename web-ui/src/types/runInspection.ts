/** Read-only Run Inspector contract (mirrors app/services/run_inspector.py, version 1). */

export interface RunInspectionToolCall {
  tool: string;
  tool_call_id?: string | null;
  params?: string | null;
}

export interface RunInspectionStep {
  first_seq: number;
  last_seq: number;
  iteration: number | null;
  kind: 'tool' | 'reasoning' | string;
  status?: string | null;
  label?: string | null;
  tools: RunInspectionToolCall[];
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  result?: string | null;
  success?: boolean | null;
  self_correction?: string | null;
}

export interface RunInspectionEvent {
  seq: number;
  at: string;
  type: 'artifact' | 'error' | 'steer' | 'job' | string;
  path?: string | null;
  display_name?: string | null;
  source_tool?: string | null;
  iteration?: number | null;
  message?: string | null;
  error_type?: string | null;
  status?: string | null;
  job_id?: string | null;
}

export interface RunInspectionUsageGroup {
  phase: string;
  purpose: string;
  calls: number;
  total_tokens: number;
  duration_ms: number;
  failed: number;
}

export interface RunInspection {
  version: number;
  run: {
    run_id: string;
    session_id: string;
    status: string;
    created_at?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    duration_ms?: number | null;
    error?: string | null;
    last_event_seq?: number | null;
    user_message_id?: number | null;
    assistant_message_id?: number | null;
    message?: string | null;
    context_flags: Record<string, boolean>;
    /** Present when the run was resolved from a plan task (`GET /plans/{plan_id}/tasks/{task_id}/run-inspection`). */
    plan_id?: number | null;
    task_id?: number | null;
    task_name?: string | null;
  };
  summary: {
    steps: number;
    tool_steps: number;
    tool_failures: number;
    tools: string[];
    artifacts: number;
    errors: number;
    event_counts: Record<string, number>;
    streaming_events: Record<string, number>;
  };
  steps: RunInspectionStep[];
  events: RunInspectionEvent[];
  final: Record<string, unknown> & { response_preview?: string | null };
  usage: {
    calls: number;
    failed_calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cache_read_tokens: number;
    duration_ms: number;
    estimated_cost: number;
    cost_currency?: string | null;
    models: string[];
    by_purpose: RunInspectionUsageGroup[];
    /** Plan tasks: the ledger is keyed by task, so totals span every attempt. */
    scope?: 'run' | 'plan_task_all_attempts';
  };
  truncated: { steps: boolean; artifacts: boolean; logs?: boolean };
}

/** A chat run, addressed by its run id or by the persisted assistant message it produced. */
export interface ChatRunInspectionTarget {
  run_id?: string;
  message_id?: number;
}

/** The latest (or a specific `jobId`) run that executed one plan task; needs no chat session. */
export interface PlanTaskRunInspectionTarget {
  planId: number;
  taskId: number;
  jobId?: string;
  taskName?: string;
}

export type RunInspectionTarget = ChatRunInspectionTarget | PlanTaskRunInspectionTarget;
