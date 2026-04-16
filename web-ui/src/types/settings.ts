export interface ChatSessionSettings {
  [key: string]: unknown;
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

export interface ContextSection {
  task_id: number;
  name: string;
  short_name: string;
  kind: string;
  content: string;
}
