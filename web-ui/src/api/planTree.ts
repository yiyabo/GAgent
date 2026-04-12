import { BaseApi } from './client';
import type {
  PlanExecutionSummary,
  PlanResultItem,
  PlanResultsResponse,
  PlanSummary,
  PlanTreeResponse,
  DependencyPlanResponse,
    ExecuteTaskResponse,
    VerifyTaskResponse,
    DecompositionJobStatus,
  JobLogTailResponse,
  BackgroundTaskBoardResponse,
  TodoListResponse,
} from '@/types';

interface SubgraphResponse {
  plan_id: number;
  root_node: number;
  max_depth: number;
  outline: string;
  nodes: Array<Record<string, any>>;
}

export interface DecomposeTaskPayload {
  plan_id: number;
  expand_depth?: number;
  node_budget?: number;
  allow_existing_children?: boolean;
  async_mode?: boolean;
}

interface DecomposeTaskResponse {
  success: boolean;
  message: string;
  result: Record<string, any>;
  job?: DecompositionJobStatus | null;
}

export interface ExecuteTaskWithDepsPayload {
  include_dependencies?: boolean;
  include_subtasks?: boolean;
  deep_think?: boolean;
  async_mode?: boolean;
  session_id?: string;
}

export interface JobControlPayload {
  action: 'pause' | 'resume' | 'skip_step';
}

export interface JobControlResponse {
  success: boolean;
  job_id: string;
  action: string;
  status?: string | null;
  message: string;
}

class PlanTreeApi extends BaseApi {
  listPlans = async (): Promise<PlanSummary[]> => {
  return this.get<PlanSummary[]>('/plans');
  };

  getPlanTree = async (planId: number): Promise<PlanTreeResponse> => {
  return this.get<PlanTreeResponse>(`/plans/${planId}/tree`);
  };

  getPlanSubgraph = async (
  planId: number,
  nodeId: number,
  maxDepth = 2
  ): Promise<SubgraphResponse> => {
  return this.get<SubgraphResponse>(`/plans/${planId}/subgraph`, {
  node_id: nodeId,
  max_depth: maxDepth,
  });
  };

  decomposeTask = async (
  taskId: number,
  payload: DecomposeTaskPayload
  ): Promise<DecomposeTaskResponse> => {
  return this.post<DecomposeTaskResponse>(`/tasks/${taskId}/decompose`, payload);
  };

  getDecompositionJobStatus = async (jobId: string): Promise<DecompositionJobStatus> => {
  return this.getJobStatus(jobId);
  };

  getJobStatus = async (jobId: string): Promise<DecompositionJobStatus> => {
  return this.get<DecompositionJobStatus>(`/jobs/${jobId}`);
  };

  getJobLogTail = async (jobId: string, tail = 200): Promise<JobLogTailResponse> => {
  return this.get<JobLogTailResponse>(`/jobs/${jobId}/logs`, { tail });
  };

  getBackgroundTaskBoard = async (options?: {
  limit?: number;
  session_id?: string;
  plan_id?: number;
  include_finished?: boolean;
  }): Promise<BackgroundTaskBoardResponse> => {
  return this.get<BackgroundTaskBoardResponse>('/jobs/board', options);
  };

  controlJob = async (
    jobId: string,
    payload: JobControlPayload
  ): Promise<JobControlResponse> => {
  return this.post<JobControlResponse>(`/jobs/${jobId}/control`, payload);
  };

  getPlanResults = async (
  planId: number,
  options?: { onlyWithOutput?: boolean }
  ): Promise<PlanResultsResponse> => {
  return this.get<PlanResultsResponse>(`/plans/${planId}/results`, {
  only_with_output: options?.onlyWithOutput ?? true,
  });
  };

  getTaskResult = async (planId: number, taskId: number): Promise<PlanResultItem> => {
  return this.get<PlanResultItem>(`/tasks/${taskId}/result`, {
  plan_id: planId,
  });
  };

  getPlanExecutionSummary = async (planId: number): Promise<PlanExecutionSummary> => {
  return this.get<PlanExecutionSummary>(`/plans/${planId}/execution/summary`);
  };

  getTaskDependencyPlan = async (
  planId: number,
  taskId: number,
  options?: {
    include_dependencies?: boolean;
    include_subtasks?: boolean;
  }
  ): Promise<DependencyPlanResponse> => {
  return this.get<DependencyPlanResponse>(`/tasks/${taskId}/dependency-plan`, {
  plan_id: planId,
  include_dependencies: options?.include_dependencies,
  include_subtasks: options?.include_subtasks,
  });
  };

  executeTaskWithDeps = async (
  planId: number,
  taskId: number,
  payload: ExecuteTaskWithDepsPayload = {}
  ): Promise<ExecuteTaskResponse> => {
  return this.post<ExecuteTaskResponse>(`/tasks/${taskId}/execute?plan_id=${planId}`, payload);
  };

  verifyTask = async (planId: number, taskId: number): Promise<VerifyTaskResponse> => {
  return this.post<VerifyTaskResponse>(`/tasks/${taskId}/verify?plan_id=${planId}`, {});
  };

  getPlanTodoList = async (
    planId: number,
    targetTaskId: number,
    options?: { expandComposites?: boolean }
  ): Promise<TodoListResponse> => {
    return this.get<TodoListResponse>(`/plans/${planId}/todo-list`, {
      target_task_id: targetTaskId,
      expand_composites: options?.expandComposites,
    });
  };
}

export const planTreeApi = new PlanTreeApi();

interface WaitForJobOptions {
  intervalMs?: number;
  timeoutMs?: number;
  onUpdate?: (status: DecompositionJobStatus) => void;
}

export const waitForDecompositionJob = async (
  jobId: string,
  options: WaitForJobOptions = {}
): Promise<DecompositionJobStatus> => {
  const interval = Math.max(options.intervalMs ?? 2000, 500);
  const timeout = Math.max(options.timeoutMs ?? 300000, interval);
  const startedAt = Date.now();

  // eslint-disable-next-line no-constant-condition
  while (true) {
  const status = await planTreeApi.getDecompositionJobStatus(jobId);
  options.onUpdate?.(status);

  if (status.status === 'succeeded') {
  return status;
  }
  if (status.status === 'failed') {
  throw new Error(status.error || 'Plan decomposition failed');
  }

  if (Date.now() - startedAt > timeout) {
  throw new Error('Timed out while waiting for plan decomposition to finish.');
  }

  await new Promise((resolve) => setTimeout(resolve, interval));
  }
};
