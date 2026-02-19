import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';
import type { Memory, MemoryStats } from '@/types';

interface MemoryState {
  memories: Memory[];
  selectedMemory: Memory | null;
  stats: MemoryStats | null;

  filters: {
    search_query: string;
    memory_types: string[];
    importance_levels: string[];
    min_similarity: number;
  };

  loading: boolean;
  error: string | null;

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

  getFilteredMemories: () => Memory[];
}

export const useMemoryStore = create<MemoryState>()(
  subscribeWithSelector((set, get) => ({
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

    setMemories: (memories) => set({ memories }),

    addMemory: (memory) => set((state) => ({
      memories: [memory, ...state.memories],
    })),

    updateMemory: (id, updates) => set((state) => ({
      memories: state.memories.map((m) =>
        m.id === id ? { ...m, ...updates } : m
      ),
      selectedMemory: state.selectedMemory?.id === id
        ? { ...state.selectedMemory, ...updates }
        : state.selectedMemory,
    })),

    removeMemory: (id) => set((state) => ({
      memories: state.memories.filter((m) => m.id !== id),
      selectedMemory: state.selectedMemory?.id === id ? null : state.selectedMemory,
    })),

    setSelectedMemory: (memory) => set({ selectedMemory: memory }),

    setStats: (stats) => set({ stats }),

    setFilters: (filters) => set((state) => ({
      filters: { ...state.filters, ...filters },
    })),

    clearFilters: () => set({
      filters: {
        search_query: '',
        memory_types: [],
        importance_levels: [],
        min_similarity: 0.6,
      },
    }),

    setLoading: (loading) => set({ loading }),

    setError: (error) => set({ error }),

    getFilteredMemories: () => {
      const { memories, filters } = get();
      return memories.filter((memory) => {
        if (filters.search_query) {
          const query = filters.search_query.toLowerCase();
          const matchContent = memory.content.toLowerCase().includes(query);
          const matchKeywords = memory.keywords.some(k => k.toLowerCase().includes(query));
          const matchTags = memory.tags.some(t => t.toLowerCase().includes(query));
          if (!matchContent && !matchKeywords && !matchTags) {
            return false;
          }
        }

        if (filters.memory_types.length > 0 && !filters.memory_types.includes(memory.memory_type)) {
          return false;
        }

        if (filters.importance_levels.length > 0 && !filters.importance_levels.includes(memory.importance)) {
          return false;
        }

        if (memory.similarity !== undefined && memory.similarity < filters.min_similarity) {
          return false;
        }

        return true;
      });
    },
  }))
);
