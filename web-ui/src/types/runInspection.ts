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

/** One delegated sub-agent run (llm_usage_log rows whose parent_run_id is this run). */
export interface RunInspectionChildRun {
  child_run_id: string;
  call_count: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens: number;
  duration_ms: number;
  ok_calls?: number;
  error_calls?: number;
  estimated_cost?: number;
  models?: string[];
  call_purposes?: string[];
  call_statuses?: string[];
  first_call_at?: string | null;
  last_call_at?: string | null;
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
  };
  child_runs?: RunInspectionChildRun[];
  truncated: { steps: boolean; artifacts: boolean };
}

export interface RunInspectionTarget {
  run_id?: string;
  message_id?: number;
}
