// 上传文件相关类型
export interface UploadedFile {
  file_id: string;
  file_path: string;
  file_name: string;
  original_name: string;
  file_size: string;
  file_type: string;
  uploaded_at: string;
  category?: string;
  is_archive?: boolean;
  extracted_path?: string;
  extracted_files?: number;
}

// API 响应类型
export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  message?: string;
  error?: string;
}

// 系统状态类型
export interface SystemStatus {
  api_connected: boolean;
  database_status: 'connected' | 'disconnected' | 'error';
  active_tasks: number;
  total_plans: number;
  system_load: {
    cpu: number;
    memory: number;
    api_calls_per_minute: number;
  };
}

// Memory-MCP 相关类型
export interface Memory {
  id: string;
  content: string;
  memory_type: 'conversation' | 'experience' | 'knowledge' | 'context';
  importance: 'critical' | 'high' | 'medium' | 'low' | 'temporary';
  keywords: string[];
  tags: string[];
  context: string;
  related_task_id?: number;
  created_at: string;
  last_accessed?: string;
  retrieval_count: number;
  similarity?: number;
  links?: MemoryLink[];
}

export interface MemoryLink {
  memory_id: string;
  similarity: number;
}

export interface MemoryStats {
  total_memories: number;
  memory_type_distribution: Record<string, number>;
  importance_distribution: Record<string, number>;
  average_connections: number;
  embedding_coverage: number;
  evolution_count?: number;
}

export interface SaveMemoryRequest {
  content: string;
  memory_type: Memory['memory_type'];
  importance: Memory['importance'];
  tags?: string[];
  keywords?: string[];
  context?: string;
  related_task_id?: number;
}

export interface QueryMemoryRequest {
  search_text: string;
  memory_types?: Memory['memory_type'][];
  importance_levels?: Memory['importance'][];
  limit?: number;
  min_similarity?: number;
}

export interface QueryMemoryResponse {
  memories: Memory[];
  total: number;
  search_time_ms: number;
}
