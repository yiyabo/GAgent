import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { PlanResultItem, PlanSyncEventDetail, Task } from '@/types';
import { queryClient } from '@/queryClient';
import { isPlanSyncEventDetail } from '@utils/planSyncEvents';

// 临时类型定义，解决编译错误
interface TaskStats {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
}

interface DAGNode {
  id: string;
  label: string;
  color?: string;
  shape?: string;
}

interface DAGEdge {
  from: string;
  to: string;
  color?: string;
  label?: string;
}

interface TasksState {
  // 任务数据
  tasks: Task[];
  selectedTask: Task | null;
  selectedTaskId: number | null;
  taskStats: TaskStats | null;
  currentPlan: string | null;
  currentWorkflowId: string | null;
  isTaskDrawerOpen: boolean;
  taskResultCache: Record<number, PlanResultItem | null>;
  
  // DAG可视化数据
  dagNodes: DAGNode[];
  dagEdges: DAGEdge[];
  dagLayout: 'hierarchical' | 'force' | 'circular';
  
  // 过滤和搜索
  filters: {
    status: string[];
    task_type: string[];
    search_query: string;
  };
  
  // 操作方法
  setTasks: (tasks: Task[]) => void;
  addTask: (task: Task) => void;
  updateTask: (id: number, updates: Partial<Task>) => void;
  removeTask: (id: number) => void;
  setSelectedTask: (task: Task | null) => void;
  openTaskDrawer: (task: Task | null) => void;
  openTaskDrawerById: (taskId: number) => void;
  closeTaskDrawer: () => void;
  setTaskResult: (taskId: number, result: PlanResultItem | null) => void;
  clearTaskResultCache: (taskId?: number) => void;
  setCurrentPlan: (planTitle: string | null) => void;
  setCurrentWorkflowId: (workflowId: string | null) => void;
  setTaskStats: (stats: TaskStats | null) => void;
  
  // DAG操作
  setDagData: (nodes: DAGNode[], edges: DAGEdge[]) => void;
  updateNodePosition: (nodeId: string, position: { x: number; y: number }) => void;
  setDagLayout: (layout: TasksState['dagLayout']) => void;
  
  // 过滤操作
  setFilters: (filters: Partial<TasksState['filters']>) => void;
  clearFilters: () => void;
  
  // 计算属性
  getFilteredTasks: () => Task[];
  getTaskStats: () => {
    total: number;
    pending: number;
    running: number;
    completed: number;
    failed: number;
  };
}

export const useTasksStore = create<TasksState>()(
  subscribeWithSelector((set, get) => ({
    // 初始状态
    tasks: [],
    selectedTask: null,
    selectedTaskId: null,
    taskStats: null,
    currentPlan: null,
    currentWorkflowId: null,
    isTaskDrawerOpen: false,
    taskResultCache: {},
    dagNodes: [],
    dagEdges: [],
    dagLayout: 'hierarchical',
    filters: {
      status: [],
      task_type: [],
      search_query: '',
    },

    // 设置任务列表
    setTasks: (tasks) => {
      set(() => {
        const { nodes, edges } = generateDagData(tasks);
        const rootTask = tasks.find((task) => task.task_type === 'root');
        const selectedId = get().selectedTaskId;
        const matchedSelection =
          selectedId != null ? tasks.find((task) => task.id === selectedId) ?? null : null;
        const nextSelectedTask = matchedSelection ?? null;
        const nextSelectedId = matchedSelection ? selectedId : null;
        return {
          tasks,
          dagNodes: nodes,
          dagEdges: edges,
          currentPlan: rootTask?.name ?? null,
          selectedTask: nextSelectedTask,
          selectedTaskId: nextSelectedId,
        };
      });
    },

    // 添加任务
    addTask: (task) => set((state) => {
      const tasks = [...state.tasks, task];
      const { nodes, edges } = generateDagData(tasks);
      return { tasks, dagNodes: nodes, dagEdges: edges };
    }),

    // 更新任务
    updateTask: (id, updates) => set((state) => {
      const tasks = state.tasks.map((task) =>
        task.id === id ? { ...task, ...updates } : task
      );
      const { nodes, edges } = generateDagData(tasks);
      const isSelected = state.selectedTaskId === id;
      return { 
        tasks, 
        dagNodes: nodes, 
        dagEdges: edges,
        selectedTask: isSelected && state.selectedTask
          ? { ...state.selectedTask, ...updates }
          : state.selectedTask,
      };
    }),

    // 删除任务
    removeTask: (id) => set((state) => {
      const tasks = state.tasks.filter((task) => task.id !== id);
      const { nodes, edges } = generateDagData(tasks);
      const removingSelected = state.selectedTaskId === id;
      return { 
        tasks, 
        dagNodes: nodes, 
        dagEdges: edges,
        selectedTask: removingSelected ? null : state.selectedTask,
        selectedTaskId: removingSelected ? null : state.selectedTaskId,
      };
    }),

    // 设置选中任务
    setSelectedTask: (task) =>
      set({
        selectedTask: task,
        selectedTaskId: task?.id ?? null,
      }),

    openTaskDrawer: (task) =>
      set((state) => {
        if (!task) {
          return {
            isTaskDrawerOpen: false,
            selectedTask: null,
            selectedTaskId: null,
          };
        }
        return {
          isTaskDrawerOpen: true,
          selectedTask: task,
          selectedTaskId: task.id,
        };
      }),

    openTaskDrawerById: (taskId) =>
      set((state) => {
        const task =
          state.tasks.find((item) => item.id === taskId) ??
          (state.selectedTask?.id === taskId ? state.selectedTask : null);
        return {
          isTaskDrawerOpen: true,
          selectedTask: task ?? null,
          selectedTaskId: taskId,
        };
      }),

    closeTaskDrawer: () =>
      set({
        isTaskDrawerOpen: false,
        selectedTaskId: null,
      }),

    setTaskResult: (taskId, result) =>
      set((state) => {
        const nextCache = { ...state.taskResultCache };
        if (result == null) {
          delete nextCache[taskId];
        } else {
          nextCache[taskId] = result;
        }
        return { taskResultCache: nextCache };
      }),

    clearTaskResultCache: (taskId) =>
      set((state) => {
        if (typeof taskId === 'number') {
          if (!(taskId in state.taskResultCache)) {
            return {};
          }
          const nextCache = { ...state.taskResultCache };
          delete nextCache[taskId];
          return { taskResultCache: nextCache };
        }
        if (Object.keys(state.taskResultCache).length === 0) {
          return {};
        }
        return { taskResultCache: {} };
      }),

    // 设置当前计划
    setCurrentPlan: (planTitle) => set({ currentPlan: planTitle }),

    setCurrentWorkflowId: (workflowId) => set({ currentWorkflowId: workflowId }),

    setTaskStats: (stats) => set({ taskStats: stats }),

    // 设置DAG数据
    setDagData: (nodes, edges) => set({ dagNodes: nodes, dagEdges: edges }),

    // 更新节点位置
    updateNodePosition: (nodeId, position) => set((state) => ({
      dagNodes: state.dagNodes.map((node) =>
        node.id === nodeId ? { ...node, ...position } : node
      ),
    })),

    // 设置DAG布局
    setDagLayout: (layout) => set({ dagLayout: layout }),

    // 设置过滤器
    setFilters: (filters) => set((state) => ({
      filters: { ...state.filters, ...filters },
    })),

    // 清空过滤器
    clearFilters: () => set({
      filters: {
        status: [],
        task_type: [],
        search_query: '',
      },
    }),

    // 获取过滤后的任务
    getFilteredTasks: () => {
      const { tasks, filters } = get();
      return tasks.filter((task) => {
        // 状态过滤
        if (filters.status.length > 0 && !filters.status.includes(task.status)) {
          return false;
        }
        
        // 类型过滤
        if (filters.task_type.length > 0 && !filters.task_type.includes(task.task_type)) {
          return false;
        }
        
        // 搜索过滤
        if (filters.search_query) {
          const query = filters.search_query.toLowerCase();
          return task.name.toLowerCase().includes(query);
        }
        
        return true;
      });
    },

    // 获取任务统计
    getTaskStats: () => {
      const tasks = get().tasks;
      return {
        total: tasks.length,
        pending: tasks.filter(t => t.status === 'pending').length,
        running: tasks.filter(t => t.status === 'running').length,
        completed: tasks.filter(t => t.status === 'completed').length,
        failed: tasks.filter(t => t.status === 'failed').length,
      };
    },
  }))
);

// 生成DAG可视化数据的辅助函数
function generateDagData(tasks: Task[]): { nodes: DAGNode[]; edges: DAGEdge[] } {
  const nodes: DAGNode[] = tasks.map((task) => ({
    id: task.id.toString(),
    label: task.name.replace(/^\[.*?\]\s*/, ''), // 移除计划前缀
    group: task.task_type,
    status: task.status,
    level: task.depth,
  }));

  const edges: DAGEdge[] = [];
  
  // 基于parent_id生成边
  tasks.forEach((task) => {
    if (task.parent_id) {
      edges.push({
        from: task.parent_id.toString(),
        to: task.id.toString(),
        label: 'contains',
        color: '#1890ff',
      });
    }
  });

  return { nodes, edges };
}

const invalidatePlanCollections = () => {
  queryClient.invalidateQueries({ queryKey: ['planTree', 'summaries'], exact: false });
  queryClient.invalidateQueries({ queryKey: ['planTree', 'titles'], exact: false });
  void queryClient.refetchQueries({
    queryKey: ['planTree', 'summaries'],
    exact: false,
    type: 'active',
  });
  void queryClient.refetchQueries({
    queryKey: ['planTree', 'titles'],
    exact: false,
    type: 'active',
  });
};

const matchesPlanScopedKey = (queryKey: unknown, planId: number) => {
  if (!Array.isArray(queryKey) || queryKey.length < 2) {
    return false;
  }
  if (queryKey[0] !== 'planTree') {
    return false;
  }
  const scope = queryKey[1];
  switch (scope) {
    case 'tasks':
    case 'results':
    case 'execution':
    case 'taskResult':
    case 'full':
    case 'subgraph':
      return queryKey[2] === planId;
    default:
      return false;
  }
};

const invalidatePlanScopedQueries = (planId: number) => {
  const predicate = ({ queryKey }: { queryKey: unknown }) =>
    matchesPlanScopedKey(queryKey, planId);
  queryClient.invalidateQueries({
    predicate: ({ queryKey }) => matchesPlanScopedKey(queryKey, planId),
  });
  void queryClient.refetchQueries({
    predicate,
    type: 'active',
  });
};

const removePlanScopedQueries = (planId: number) => {
  queryClient.removeQueries({
    predicate: ({ queryKey }) => matchesPlanScopedKey(queryKey, planId),
  });
};

declare global {
  interface Window {
    __gaPlanSyncListenerRegistered__?: boolean;
  }
}

if (typeof window !== 'undefined' && !window.__gaPlanSyncListenerRegistered__) {
  const handlePlanSyncEvent = (event: CustomEvent<PlanSyncEventDetail>) => {
    const detail = event.detail;
    if (!isPlanSyncEventDetail(detail)) {
      return;
    }
    switch (detail.type) {
      case 'plan_created':
      case 'plan_updated': {
        invalidatePlanCollections();
        if (detail.plan_id != null) {
          invalidatePlanScopedQueries(detail.plan_id);
        }
        break;
      }
      case 'plan_deleted': {
        invalidatePlanCollections();
        if (detail.plan_id != null) {
          removePlanScopedQueries(detail.plan_id);
        }
        break;
      }
      case 'task_changed':
      case 'plan_jobs_completed': {
        if (detail.plan_id != null) {
          invalidatePlanScopedQueries(detail.plan_id);
        }
        break;
      }
      default:
        break;
    }
  };

  window.addEventListener('tasksUpdated', handlePlanSyncEvent as EventListener);
  window.__gaPlanSyncListenerRegistered__ = true;
}
