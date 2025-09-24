import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import { Task } from '@/types';

// 临时类型定义，解决编译错误
interface TaskStats {
  total: number;
  pending: number;
  running: number;
  done: number;
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
}

interface TasksState {
  // 任务数据
  tasks: Task[];
  selectedTask: Task | null;
  taskStats: TaskStats | null;
  currentPlan: string | null;
  
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
  setCurrentPlan: (planTitle: string | null) => void;
  
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
    taskStats: null,
    currentPlan: null,
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
      set({ tasks });
      // 自动生成DAG数据
      const { nodes, edges } = generateDagData(tasks);
      set({ dagNodes: nodes, dagEdges: edges });
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
      return { 
        tasks, 
        dagNodes: nodes, 
        dagEdges: edges,
        selectedTask: state.selectedTask?.id === id 
          ? { ...state.selectedTask, ...updates } 
          : state.selectedTask
      };
    }),

    // 删除任务
    removeTask: (id) => set((state) => {
      const tasks = state.tasks.filter((task) => task.id !== id);
      const { nodes, edges } = generateDagData(tasks);
      return { 
        tasks, 
        dagNodes: nodes, 
        dagEdges: edges,
        selectedTask: state.selectedTask?.id === id ? null : state.selectedTask
      };
    }),

    // 设置选中任务
    setSelectedTask: (task) => set({ selectedTask: task }),

    // 设置当前计划
    setCurrentPlan: (planTitle) => set({ currentPlan: planTitle }),

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
