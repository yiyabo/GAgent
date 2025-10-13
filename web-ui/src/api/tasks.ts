import { BaseApi } from './client';
import { mergeWithScope, resolveScopeParams, ScopeOverrides } from './scope';
import { Task, TaskInput, TaskOutput, ExecutionRequest, ExecutionResult } from '../types/index';

export class TasksApi extends BaseApi {
  // 获取所有任务 - 使用箭头函数确保this绑定
  getAllTasks = async (filters?: ScopeOverrides): Promise<Task[]> => {
    return this.get<Task[]>('/tasks', resolveScopeParams(filters));
  }

  // 获取任务详情
  getTask = async (id: number): Promise<Task> => {
    return this.get<Task>(`/tasks/${id}`);
  }

  // 获取任务输入
  getTaskInput = async (id: number): Promise<TaskInput> => {
    return this.get<TaskInput>(`/tasks/${id}/input`);
  }

  // 获取任务输出
  getTaskOutput = async (id: number): Promise<TaskOutput> => {
    return this.get<TaskOutput>(`/tasks/${id}/output`);
  }

  // 更新任务状态
  updateTaskStatus = async (id: number, status: Task['status']): Promise<void> => {
    return this.put<void>(`/tasks/${id}/status`, mergeWithScope({ status }));
  }

  // 删除任务
  deleteTask = async (id: number): Promise<void> => {
    return this.delete<void>(`/tasks/${id}`);
  }

  // 获取任务层次结构
  getTaskHierarchy = async (planTitle?: string): Promise<Task[]> => {
    const params = planTitle ? { plan: planTitle } : undefined;
    return this.get<Task[]>(`/tasks/hierarchy`, mergeWithScope(params));
  }

  // 执行任务
  executeTasks = async (request: ExecutionRequest): Promise<ExecutionResult[]> => {
    return this.post<ExecutionResult[]>('/run', mergeWithScope(request));
  }

  // 执行单个任务
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

  // 带评估执行任务
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

  // 工具增强执行
  executeTaskWithTools = async (
    id: number,
    options: {
      use_context?: boolean;
      context_options?: any;
    } = {}
  ): Promise<ExecutionResult> => {
    return this.post<ExecutionResult>(`/tasks/${id}/execute/tool-enhanced`, mergeWithScope(options));
  }

  // 重新运行任务
  rerunTask = async (id: number): Promise<ExecutionResult> => {
    return this.post<ExecutionResult>(`/tasks/${id}/rerun`, mergeWithScope());
  }

  // 获取任务统计 - 使用箭头函数确保this绑定
  getTaskStats = async (filters?: ScopeOverrides): Promise<{
    total: number;
    by_status: Record<string, number>;
    by_type: Record<string, number>;
  }> => {
    return this.get('/tasks/stats', resolveScopeParams(filters));
  }

  // 搜索任务 - 支持会话级过滤，实现"专事专办"
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
    
    // 合并作用域参数，实现会话级任务隔离
    const scopeParams = resolveScopeParams(scope);
    Object.entries(scopeParams).forEach(([key, value]) => {
      if (value) params.append(key, value);
    });
    
    return this.get(`/tasks/search?${params.toString()}`);
  }
}

export const tasksApi = new TasksApi();
