import { BaseApi } from './client';
import { Task, TaskInput, TaskOutput, ExecutionRequest, ExecutionResult } from '../types/index';

export class TasksApi extends BaseApi {
  // 获取所有任务
  async getAllTasks(): Promise<Task[]> {
    return this.get<Task[]>('/tasks');
  }

  // 获取任务详情
  async getTask(id: number): Promise<Task> {
    return this.get<Task>(`/tasks/${id}`);
  }

  // 获取任务输入
  async getTaskInput(id: number): Promise<TaskInput> {
    return this.get<TaskInput>(`/tasks/${id}/input`);
  }

  // 获取任务输出
  async getTaskOutput(id: number): Promise<TaskOutput> {
    return this.get<TaskOutput>(`/tasks/${id}/output`);
  }

  // 更新任务状态
  async updateTaskStatus(id: number, status: Task['status']): Promise<void> {
    return this.put<void>(`/tasks/${id}/status`, { status });
  }

  // 删除任务
  async deleteTask(id: number): Promise<void> {
    return this.delete<void>(`/tasks/${id}`);
  }

  // 获取任务层次结构
  async getTaskHierarchy(planTitle?: string): Promise<Task[]> {
    const params = planTitle ? `?plan=${encodeURIComponent(planTitle)}` : '';
    return this.get<Task[]>(`/tasks/hierarchy${params}`);
  }

  // 执行任务
  async executeTasks(request: ExecutionRequest): Promise<ExecutionResult[]> {
    return this.post<ExecutionResult[]>('/run', request);
  }

  // 执行单个任务
  async executeTask(
    id: number,
    options: {
      use_context?: boolean;
      evaluation_mode?: string;
      use_tools?: boolean;
    } = {}
  ): Promise<ExecutionResult> {
    return this.post<ExecutionResult>(`/tasks/${id}/execute`, options);
  }

  // 带评估执行任务
  async executeTaskWithEvaluation(
    id: number,
    options: {
      evaluation_mode?: 'llm' | 'multi_expert' | 'adversarial';
      max_iterations?: number;
      quality_threshold?: number;
      use_context?: boolean;
    }
  ): Promise<ExecutionResult> {
    return this.post<ExecutionResult>(`/tasks/${id}/execute/evaluation`, options);
  }

  // 工具增强执行
  async executeTaskWithTools(
    id: number,
    options: {
      use_context?: boolean;
      context_options?: any;
    } = {}
  ): Promise<ExecutionResult> {
    return this.post<ExecutionResult>(`/tasks/${id}/execute/tool-enhanced`, options);
  }

  // 重新运行任务
  async rerunTask(id: number): Promise<ExecutionResult> {
    return this.post<ExecutionResult>(`/tasks/${id}/rerun`);
  }

  // 获取任务统计
  async getTaskStats(): Promise<{
    total: number;
    by_status: Record<string, number>;
    by_type: Record<string, number>;
  }> {
    return this.get('/tasks/stats');
  }

  // 搜索任务
  async searchTasks(query: string, filters?: {
    status?: string;
    task_type?: string;
    plan_title?: string;
  }): Promise<Task[]> {
    const params = new URLSearchParams({ q: query });
    if (filters) {
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.append(key, value);
      });
    }
    return this.get(`/tasks/search?${params.toString()}`);
  }
}

export const tasksApi = new TasksApi();
