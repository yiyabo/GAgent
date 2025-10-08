import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { tasksApi } from '@api/tasks';
import { useTasksStore } from '@store/tasks';
import { Task } from '../types/index';

// 获取所有任务的Hook
export const useAllTasks = (
  filters?: { session_id?: string; workflow_id?: string },
  refetchInterval?: number
) => {
  const { setTasks } = useTasksStore();
  
  const query = useQuery({
    queryKey: ['tasks', 'all', filters?.session_id, filters?.workflow_id],
    queryFn: () => tasksApi.getAllTasks(filters),
    refetchInterval: refetchInterval || 30000, // 默认30秒刷新
    staleTime: 5000,
  });

  // 更新store中的任务数据
  useEffect(() => {
    if (query.data) {
      setTasks(query.data);
    }
  }, [query.data]); // 移除setTasks依赖，避免无限循环

  return query;
};

// 获取任务层次结构的Hook
export const useTaskHierarchy = (planTitle?: string) => {
  const { setTasks } = useTasksStore();
  
  const query = useQuery({
    queryKey: ['tasks', 'hierarchy', planTitle],
    queryFn: () => tasksApi.getTaskHierarchy(planTitle),
    enabled: !!planTitle, // 只有当planTitle存在时才执行查询
    refetchInterval: 10000, // 10秒刷新
  });

  useEffect(() => {
    if (query.data) {
      setTasks(query.data);
    }
  }, [query.data]); // 移除setTasks依赖，避免无限循环

  return query;
};

// 获取任务详情的Hook
export const useTask = (taskId: number) => {
  return useQuery({
    queryKey: ['tasks', 'detail', taskId],
    queryFn: () => tasksApi.getTask(taskId),
    enabled: !!taskId,
  });
};

// 执行任务的Hook
export const useExecuteTask = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ taskId, options }: {
      taskId: number;
      options?: {
        use_context?: boolean;
        evaluation_mode?: string;
        use_tools?: boolean;
      };
    }) => tasksApi.executeTask(taskId, options),
    onSuccess: () => {
      // 执行成功后刷新任务数据
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
};

// 更新任务状态的Hook
export const useUpdateTaskStatus = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ taskId, status }: {
      taskId: number;
      status: Task['status'];
    }) => tasksApi.updateTaskStatus(taskId, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
};

// 搜索任务的Hook
export const useSearchTasks = (query: string, filters?: {
  status?: string;
  task_type?: string;
  plan_title?: string;
}, scope?: { session_id?: string; workflow_id?: string }) => {
  return useQuery({
    queryKey: ['tasks', 'search', query, filters, scope?.session_id, scope?.workflow_id],
    queryFn: () => tasksApi.searchTasks(query, filters, scope),
    enabled: query.length > 2, // 至少输入3个字符才搜索
  });
};

// 获取任务统计的Hook
export const useTaskStats = (filters?: { session_id?: string; workflow_id?: string }) => {
  return useQuery({
    queryKey: ['tasks', 'stats', filters?.session_id, filters?.workflow_id],
    queryFn: () => tasksApi.getTaskStats(filters),
    refetchInterval: 15000, // 15秒刷新统计数据
  });
};
