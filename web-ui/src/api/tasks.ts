import { BaseApi } from './client';
import { mergeWithScope, resolveScopeParams, ScopeOverrides } from './scope';
import { Task, TaskInput, TaskOutput, ExecutionRequest, ExecutionResult } from '../types/index';

export class TasksApi extends BaseApi {
  getAllTasks = async (filters?: ScopeOverrides): Promise<Task[]> => {
    return this.get<Task[]>('/tasks', resolveScopeParams(filters));
  }

  getTask = async (id: number): Promise<Task> => {
    return this.get<Task>(`/tasks/${id}`);
  }

  getTaskInput = async (id: number): Promise<TaskInput> => {
    return this.get<TaskInput>(`/tasks/${id}/input`);
  }

  getTaskOutput = async (id: number): Promise<TaskOutput> => {
    return this.get<TaskOutput>(`/tasks/${id}/output`);
  }

  updateTaskStatus = async (id: number, status: Task['status']): Promise<void> => {
    return this.put<void>(`/tasks/${id}/status`, mergeWithScope({ status }));
  }

  deleteTask = async (id: number): Promise<void> => {
    return this.delete<void>(`/tasks/${id}`);
  }

  getTaskHierarchy = async (planTitle?: string): Promise<Task[]> => {
    const params = planTitle ? { plan: planTitle } : undefined;
    return this.get<Task[]>(`/tasks/hierarchy`, mergeWithScope(params));
  }

  executeTasks = async (request: ExecutionRequest): Promise<ExecutionResult[]> => {
    return this.post<ExecutionResult[]>('/run', mergeWithScope(request));
  }

  executeTask = async (
    id: number,
    options: {
      use_context?: boolean;
      evaluation_mode?: string;
      use_tools?: boolean;
    } = {}
  ): Promise<ExecutionResult> => {
    return this.post<ExecutionResult>(`/tasks/${id}/execute`, mergeWithScope(options));
  }

  executeTaskWithEvaluation = async (
    id: number,
    options: {
      evaluation_mode?: 'llm' | 'multi_expert' | 'adversarial';
      max_iterations?: number;
      quality_threshold?: number;
      use_context?: boolean;
    }
  ): Promise<ExecutionResult> => {
    return this.post<ExecutionResult>(`/tasks/${id}/execute/evaluation`, mergeWithScope(options));
  }

  executeTaskWithTools = async (
    id: number,
    options: {
      use_context?: boolean;
      context_options?: any;
    } = {}
  ): Promise<ExecutionResult> => {
    return this.post<ExecutionResult>(`/tasks/${id}/execute/tool-enhanced`, mergeWithScope(options));
  }

  rerunTask = async (id: number): Promise<ExecutionResult> => {
    return this.post<ExecutionResult>(`/tasks/${id}/rerun`, mergeWithScope());
  }

  getTaskStats = async (filters?: ScopeOverrides): Promise<{
    total: number;
    by_status: Record<string, number>;
    by_type: Record<string, number>;
  }> => {
    return this.get('/tasks/stats', resolveScopeParams(filters));
  }

  searchTasks = async (query: string, filters?: {
    status?: string;
    task_type?: string;
    plan_title?: string;
  }, scope?: ScopeOverrides): Promise<Task[]> => {
    const params = new URLSearchParams({ q: query });
    if (filters) {
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.append(key, value);
      });
    }
    
    const scopeParams = resolveScopeParams(scope);
    Object.entries(scopeParams).forEach(([key, value]) => {
      if (value) params.append(key, value);
    });
    
    return this.get(`/tasks/search?${params.toString()}`);
  }
}

export const tasksApi = new TasksApi();
