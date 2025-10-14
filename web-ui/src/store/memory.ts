import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import type { Memory, MemoryStats } from '@/types';

interface MemoryState {
  // 数据状态
  memories: Memory[];
  selectedMemory: Memory | null;
  stats: MemoryStats | null;

  // 过滤器状态
  filters: {
    search_query: string;
    memory_types: string[];
    importance_levels: string[];
    min_similarity: number;
  };

  // 加载状态
  loading: boolean;
  error: string | null;

  // 操作方法
  setMemories: (memories: Memory[]) => void;
  addMemory: (memory: Memory) => void;
  updateMemory: (id: string, updates: Partial<Memory>) => void;
  removeMemory: (id: string) => void;
  setSelectedMemory: (memory: Memory | null) => void;
  setStats: (stats: MemoryStats | null) => void;
  setFilters: (filters: Partial<MemoryState['filters']>) => void;
  clearFilters: () => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;

  // 计算属性
  getFilteredMemories: () => Memory[];
}

export const useMemoryStore = create<MemoryState>()(
  subscribeWithSelector((set, get) => ({
    // 初始状态
    memories: [],
    selectedMemory: null,
    stats: null,
    filters: {
      search_query: '',
      memory_types: [],
      importance_levels: [],
      min_similarity: 0.6,
    },
    loading: false,
    error: null,

    // 设置记忆列表
    setMemories: (memories) => set({ memories }),

    // 添加记忆
    addMemory: (memory) => set((state) => ({
      memories: [memory, ...state.memories],
    })),

    // 更新记忆
    updateMemory: (id, updates) => set((state) => ({
      memories: state.memories.map((m) =>
        m.id === id ? { ...m, ...updates } : m
      ),
      selectedMemory: state.selectedMemory?.id === id
        ? { ...state.selectedMemory, ...updates }
        : state.selectedMemory,
    })),

    // 删除记忆
    removeMemory: (id) => set((state) => ({
      memories: state.memories.filter((m) => m.id !== id),
      selectedMemory: state.selectedMemory?.id === id ? null : state.selectedMemory,
    })),

    // 设置选中记忆
    setSelectedMemory: (memory) => set({ selectedMemory: memory }),

    // 设置统计信息
    setStats: (stats) => set({ stats }),

    // 设置过滤器
    setFilters: (filters) => set((state) => ({
      filters: { ...state.filters, ...filters },
    })),

    // 清空过滤器
    clearFilters: () => set({
      filters: {
        search_query: '',
        memory_types: [],
        importance_levels: [],
        min_similarity: 0.6,
      },
    }),

    // 设置加载状态
    setLoading: (loading) => set({ loading }),

    // 设置错误
    setError: (error) => set({ error }),

    // 获取过滤后的记忆
    getFilteredMemories: () => {
      const { memories, filters } = get();
      return memories.filter((memory) => {
        // 搜索过滤
        if (filters.search_query) {
          const query = filters.search_query.toLowerCase();
          const matchContent = memory.content.toLowerCase().includes(query);
          const matchKeywords = memory.keywords.some(k => k.toLowerCase().includes(query));
          const matchTags = memory.tags.some(t => t.toLowerCase().includes(query));
          if (!matchContent && !matchKeywords && !matchTags) {
            return false;
          }
        }

        // 类型过滤
        if (filters.memory_types.length > 0 && !filters.memory_types.includes(memory.memory_type)) {
          return false;
        }

        // 重要性过滤
        if (filters.importance_levels.length > 0 && !filters.importance_levels.includes(memory.importance)) {
          return false;
        }

        // 相似度过滤
        if (memory.similarity !== undefined && memory.similarity < filters.min_similarity) {
          return false;
        }

        return true;
      });
    },
  }))
);
