import { BaseApi } from './client';
import { mergeWithScope, resolveScopeParams, ScopeOverrides } from './scope';
import { Plan, PlanProposal, PlanTaskNode } from '../types/index';

export class PlansApi extends BaseApi {
  proposePlan = async (proposal: PlanProposal): Promise<Plan> => {
    return this.post<Plan>('/plans/propose', proposal);
  }

  approvePlan = async (plan: Plan): Promise<void> => {
    return this.post<void>('/plans/approve', plan);
  }

  getAllPlans = async (filters?: ScopeOverrides): Promise<{ plans: string[] }> => {
    return this.get<{ plans: string[] }>('/plans', resolveScopeParams(filters));
  }

  listPlanTitles = async (filters?: ScopeOverrides): Promise<string[]> => {
    const data = await this.getAllPlans(filters);
    if (data && Array.isArray(data.plans)) {
      return data.plans;
    }
    return [];
  }

  getPlanTasks = async (title: string, filters?: ScopeOverrides): Promise<PlanTaskNode[]> => {
    return this.get<PlanTaskNode[]>(`/plans/${encodeURIComponent(title)}/tasks`, resolveScopeParams(filters));
  }

  getPlan = async (title: string): Promise<Plan> => {
    return this.get<Plan>(`/plans/${encodeURIComponent(title)}`);
  }

  deletePlan = async (title: string): Promise<void> => {
    return this.delete<void>(`/plans/${encodeURIComponent(title)}`);
  }

  getPlanStatus = async (title: string): Promise<{
    total_tasks: number;
    completed_tasks: number;
    failed_tasks: number;
    pending_tasks: number;
    progress_percentage: number;
    estimated_completion?: string;
  }> => {
    return this.get(`/plans/${encodeURIComponent(title)}/status`);
  }

  decomposePlan = async (
    title: string,
    options: {
      max_depth?: number;
      force?: boolean;
      tool_aware?: boolean;
    } = {}
  ): Promise<{
    success: boolean;
    message: string;
    new_tasks_count: number;
    total_tasks_count: number;
  }> => {
    return this.post(`/plans/${encodeURIComponent(title)}/decompose`, options);
  }

  exportPlan = async (
    title: string,
    format: 'json' | 'markdown' | 'pdf' = 'json'
  ): Promise<Blob> => {
    const response = await this.client.get(
      `/plans/${encodeURIComponent(title)}/export`,
      {
        params: { format },
        responseType: 'blob',
      }
    );
    return response.data;
  }

  copyPlan = async (sourceTitle: string, newTitle: string): Promise<Plan> => {
    return this.post<Plan>(`/plans/${encodeURIComponent(sourceTitle)}/copy`, {
      new_title: newTitle,
    });
  }

  getPlanTemplates = async (): Promise<{
    id: string;
    name: string;
    description: string;
    category: string;
    tasks_count: number;
  }[]> => {
    return this.get('/plans/templates', resolveScopeParams());
  }

  createFromTemplate = async (
    templateId: string,
    title: string,
    customizations?: Record<string, any>
  ): Promise<Plan> => {
    return this.post(`/plans/templates/${templateId}/create`, mergeWithScope({
      title,
      customizations,
    }));
  }
}

export const plansApi = new PlansApi();
