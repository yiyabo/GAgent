import { BaseApi } from './client';
import type {
  Memory,
  MemoryStats,
  SaveMemoryRequest,
  QueryMemoryRequest,
  QueryMemoryResponse,
  MemoryEntry,
  ListMemoriesParams,
  ListMemoriesResponse,
  UpdateMemoryRequest,
  UpdateMemoryResponse,
  DeleteMemoryResponse,
} from '@/types';

class MemoryApi extends BaseApi {
  /**
  * Save one memory entry.
  */
  async saveMemory(request: SaveMemoryRequest): Promise<Memory> {
  const response = await this.post<any>('/mcp/save_memory', request);

  return {
  id: response.context_id || response.memory_id || '',
  content: response.content,
  memory_type: response.memory_type,
  importance: response.meta?.importance || 'medium',
  keywords: response.meta?.agentic_keywords || [],
  tags: response.meta?.tags || [],
  context: response.meta?.agentic_context || 'General',
  related_task_id: response.task_id,
  created_at: response.created_at,
  retrieval_count: 0,
  similarity: undefined,
  };
  }

  /**
  * Query memories by filters/search.
  */
  async queryMemory(request: QueryMemoryRequest): Promise<QueryMemoryResponse> {
  const response = await this.post<any>('/mcp/query_memory', request);

  const memories: Memory[] = response.memories.map((m: any) => ({
  id: m.memory_id || m.id || '',
  content: m.content,
  memory_type: m.memory_type,
  importance: m.meta?.importance || 'medium',
  keywords: m.meta?.agentic_keywords || [],
  tags: m.meta?.tags || [],
  context: m.meta?.agentic_context || 'General',
  related_task_id: m.task_id,
  created_at: m.created_at,
  retrieval_count: m.retrieval_count || 0,
  similarity: m.similarity,
  }));

  return {
  memories,
  total: response.total,
  search_time_ms: response.search_time_ms,
  };
  }

  /**
  * Fetch memory statistics.
  */
  async getStats(): Promise<MemoryStats> {
  return this.get<MemoryStats>('/mcp/memory/stats');
  }

  /**
  * Auto-save task output as memory.
  */
  async autoSaveTask(taskId: number, taskName: string, content: string): Promise<{ success: boolean; memory_id: string }> {
  return this.post('/mcp/memory/auto_save_task', {
  task_id: taskId,
  task_name: taskName,
  content,
  });
  }

  /**
  * List stored memories for the governance drawer, newest first, with search and filters.
  * Empty filters are left out so the backend applies no constraint for them.
  */
  async listMemories(params: ListMemoriesParams = {}): Promise<ListMemoriesResponse> {
  const query: Record<string, string | number> = {
  limit: params.limit ?? 50,
  offset: params.offset ?? 0,
  };
  const search = params.search?.trim();
  if (search) query.search = search;
  if (params.memory_type) query.memory_type = params.memory_type;
  if (params.importance) query.importance = params.importance;

  const response = await this.get<any>('/mcp/memory/entries', query);
  const items: MemoryEntry[] = Array.isArray(response?.items) ? response.items.map(normalizeEntry) : [];
  return {
  items,
  total: typeof response?.total === 'number' ? response.total : items.length,
  limit: typeof response?.limit === 'number' ? response.limit : Number(query.limit),
  offset: typeof response?.offset === 'number' ? response.offset : Number(query.offset),
  };
  }

  /**
  * Edit one memory: content, importance, type and/or tags.
  */
  async updateMemory(id: string, updates: UpdateMemoryRequest): Promise<UpdateMemoryResponse> {
  const response = await this.patch<any>(`/mcp/memory/entries/${encodeURIComponent(id)}`, updates);
  return { success: Boolean(response?.success), item: normalizeEntry(response?.item ?? {}) };
  }

  /**
  * Delete one memory permanently.
  */
  async deleteMemory(id: string): Promise<DeleteMemoryResponse> {
  const response = await this.delete<any>(`/mcp/memory/entries/${encodeURIComponent(id)}`);
  return { success: Boolean(response?.success), deleted_id: String(response?.deleted_id ?? id) };
  }
}

/** Fill the list fields the backend may omit so the UI never has to guard them. */
function normalizeEntry(raw: any): MemoryEntry {
  return {
  id: String(raw?.id ?? ''),
  content: String(raw?.content ?? ''),
  memory_type: raw?.memory_type ?? 'knowledge',
  importance: raw?.importance ?? 'medium',
  keywords: Array.isArray(raw?.keywords) ? raw.keywords : [],
  tags: Array.isArray(raw?.tags) ? raw.tags : [],
  context: String(raw?.context ?? ''),
  created_at: raw?.created_at ?? null,
  last_accessed: raw?.last_accessed ?? null,
  retrieval_count: Number(raw?.retrieval_count) || 0,
  embedding_generated: Boolean(raw?.embedding_generated),
  };
}

export const memoryApi = new MemoryApi();
