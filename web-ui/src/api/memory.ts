import { BaseApi } from './client';
import type {
  Memory,
  MemoryStats,
  SaveMemoryRequest,
  QueryMemoryRequest,
  QueryMemoryResponse,
} from '@/types';

class MemoryApi extends BaseApi {
  /**
   * 保存记忆
   */
  async saveMemory(request: SaveMemoryRequest): Promise<Memory> {
    const response = await this.post<any>('/mcp/save_memory', request);

    // 转换后端响应为前端Memory类型
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
   * 查询记忆
   */
  async queryMemory(request: QueryMemoryRequest): Promise<QueryMemoryResponse> {
    const response = await this.post<any>('/mcp/query_memory', request);

    // 转换后端响应
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
   * 获取统计信息
   */
  async getStats(): Promise<MemoryStats> {
    return this.get<MemoryStats>('/mcp/memory/stats');
  }

  /**
   * 自动保存任务记忆
   */
  async autoSaveTask(taskId: number, taskName: string, content: string): Promise<{ success: boolean; memory_id: string }> {
    return this.post('/mcp/memory/auto_save_task', {
      task_id: taskId,
      task_name: taskName,
      content,
    });
  }
}

export const memoryApi = new MemoryApi();
