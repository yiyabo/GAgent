import { BaseApi } from './client';
import type {
  PlanExecutionSummary,
  PlanResultItem,
  PlanResultsResponse,
  PlanSummary,
  PlanTreeResponse,
  DecompositionJobStatus,
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
      throw new Error(status.error || '计划分解任务失败');
    }

    if (Date.now() - startedAt > timeout) {
      throw new Error('等待计划分解超时，请稍后重试。');
    }

    await new Promise((resolve) => setTimeout(resolve, interval));
  }
};
