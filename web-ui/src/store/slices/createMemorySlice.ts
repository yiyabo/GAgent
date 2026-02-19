import { ChatSliceCreator } from './types';
import { memoryApi } from '@api/memory';
import { ChatMessage, Memory } from '@/types';

export const createMemorySlice: ChatSliceCreator = (set, get) => ({
  memoryEnabled: true,
  relevantMemories: [],

  toggleMemory: () => set((state) => ({ memoryEnabled: !state.memoryEnabled })),

  setMemoryEnabled: (enabled) => set({ memoryEnabled: enabled }),

  setRelevantMemories: (memories) => set({ relevantMemories: memories }),

  saveMessageAsMemory: async (message: ChatMessage, memoryType = 'conversation', importance = 'medium') => {
  try {
  console.log('Saving memory from message:', { content: message.content.substring(0, 50) });

  await memoryApi.saveMemory({
  content: message.content,
  memory_type: memoryType as any,
  importance: importance as any,
  tags: ['chat', 'manual_saved'],
  context: `save ${new Date().toLocaleString()}`,
  related_task_id: message.metadata?.task_id
  });

  console.log('Memory saved');
  } catch (error) {
  console.error('Failed to save memory:', error);
  throw error;
  }
  },
});
