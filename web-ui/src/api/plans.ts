import { BaseApi } from './client';
import { mergeWithScope, resolveScopeParams, ScopeOverrides } from './scope';
import { Plan, PlanProposal, PlanTaskNode } from '../types/index';

export class PlansApi extends BaseApi {
  // 提议计划 - 使用箭头函数确保this绑定
  proposePlan = async (proposal: PlanProposal): Promise<Plan> => {
    return this.post<Plan>('/plans/propose', proposal);
  }

  // 批准计划
  approvePlan = async (plan: Plan): Promise<void> => {
    return this.post<void>('/plans/approve', plan);
  }

  // 获取所有计划
  getAllPlans = async (filters?: ScopeOverrides): Promise<{ plans: string[] }> => {
    return this.get<{ plans: string[] }>('/plans', resolveScopeParams(filters));
  }

  // 获取计划标题列表（便捷封装）
  listPlanTitles = async (filters?: ScopeOverrides): Promise<string[]> => {
    const data = await this.getAllPlans(filters);
    if (data && Array.isArray(data.plans)) {
      return data.plans;
    }
    return [];
  }

  // 获取计划任务
  getPlanTasks = async (title: string, filters?: ScopeOverrides): Promise<PlanTaskNode[]> => {
    return this.get<PlanTaskNode[]>(`/plans/${encodeURIComponent(title)}/tasks`, resolveScopeParams(filters));
  }

  // 获取计划详情
  getPlan = async (title: string): Promise<Plan> => {
    return this.get<Plan>(`/plans/${encodeURIComponent(title)}`);
  }

  // 删除计划
  deletePlan = async (title: string): Promise<void> => {
    return this.delete<void>(`/plans/${encodeURIComponent(title)}`);
  }

  // 获取计划状态
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

  // 递归分解计划
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

  // 导出计划
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

  // 复制计划
  copyPlan = async (sourceTitle: string, newTitle: string): Promise<Plan> => {
    return this.post<Plan>(`/plans/${encodeURIComponent(sourceTitle)}/copy`, {
      new_title: newTitle,
    });
  }

  // 获取计划模板
  getPlanTemplates = async (): Promise<{
    id: string;
    name: string;
    description: string;
    category: string;
    tasks_count: number;
  }[]> => {
    return this.get('/plans/templates', resolveScopeParams());
  }

  // 从模板创建计划
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
