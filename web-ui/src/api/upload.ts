import axios from 'axios';
import { BaseApi } from './client';

export const UPLOAD_TIMEOUT_MS = 60 * 60 * 1000;
/** Files above this size go up as numbered chunks, so a dropped connection costs one chunk, not the file. */
export const CHUNKED_UPLOAD_THRESHOLD_BYTES = 32 * 1024 * 1024;
export const UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
export const UPLOAD_CHUNK_ATTEMPTS = 6;
const CHUNK_TIMEOUT_MS = 10 * 60 * 1000;
const RETRYABLE_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

/** Waiting between attempts; tests replace it. */
export const uploadRetryTiming = {
  delay: (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
};

export type UploadProgress = (fraction: number) => void;

function isRetryableUploadError(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;
  if (!error.response) return true;
  return RETRYABLE_STATUSES.has(error.response.status);
}

function newUploadId(): string {
  return `upl_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

export interface UploadResponse {
  success: boolean;
  file_id?: string;
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
  session_id?: string;
}

export interface UploadedFileInfo {
  file_id: string;
  file_path: string;
  file_name: string;
  original_name: string;
  file_size: string;
  uploaded_at: string;
  category?: string;
  is_archive?: boolean;
  extracted_path?: string;
  extracted_files?: number;
}

export interface FileListResponse {
  success: boolean;
  files: UploadedFileInfo[];
  total: number;
  session_id: string;
}

export class UploadApi extends BaseApi {
  /**
  * uploadfile
  */
  uploadFile = async (
  file: File,
  sessionId: string,
  onProgress?: UploadProgress,
  ): Promise<UploadResponse> => {
  if (file.size > CHUNKED_UPLOAD_THRESHOLD_BYTES) {
    return this.uploadInChunks(file, sessionId, onProgress);
  }
  const formData = new FormData();
  formData.append('file', file);
  formData.append('session_id', sessionId);

  const response = await this.client.post<UploadResponse>('/upload/file', formData, {
  headers: {
  'Content-Type': 'multipart/form-data',
  },
  // Sequencing data can take minutes to send; the 30 s API default would abort it.
  timeout: UPLOAD_TIMEOUT_MS,
  onUploadProgress: (event) => {
    if (onProgress && event.total) onProgress(Math.min(1, event.loaded / event.total));
  },
  });
  return response.data;
  };

  private withRetries = async <T>(send: () => Promise<T>): Promise<T> => {
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await send();
      } catch (error) {
        if (attempt >= UPLOAD_CHUNK_ATTEMPTS || !isRetryableUploadError(error)) throw error;
        await uploadRetryTiming.delay(Math.min(30_000, 1000 * 2 ** (attempt - 1)));
      }
    }
  };

  /**
  * Send a large file as numbered chunks, retrying a chunk whose connection dropped, then ask the server to join them.
  */
  uploadInChunks = async (
  file: File,
  sessionId: string,
  onProgress?: UploadProgress,
  ): Promise<UploadResponse> => {
  const uploadId = newUploadId();
  const total = Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK_BYTES));
  const fields = {
    session_id: sessionId,
    upload_id: uploadId,
    file_name: file.name,
    total: String(total),
    total_size: String(file.size),
  };
  for (let index = 0; index < total; index += 1) {
    const start = index * UPLOAD_CHUNK_BYTES;
    const piece = file.slice(start, Math.min(file.size, start + UPLOAD_CHUNK_BYTES));
    await this.withRetries(() => {
      const formData = new FormData();
      Object.entries(fields).forEach(([key, value]) => formData.append(key, value));
      formData.append('index', String(index));
      formData.append('chunk', piece, `${file.name}.part${index}`);
      return this.client.post('/upload/chunk', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: CHUNK_TIMEOUT_MS,
        onUploadProgress: (event) => {
          if (onProgress && file.size > 0) onProgress(Math.min(1, (start + (event.loaded ?? 0)) / file.size));
        },
      });
    });
    onProgress?.(Math.min(1, (start + piece.size) / Math.max(1, file.size)));
  }
  const response = await this.withRetries(() => {
    const formData = new FormData();
    Object.entries(fields).forEach(([key, value]) => formData.append(key, value));
    return this.client.post<UploadResponse>('/upload/chunk/complete', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Joining a multi-GB file and extracting an archive happen before the reply.
      timeout: UPLOAD_TIMEOUT_MS,
    });
  });
  return response.data;
  };

  /**
  * Delete one upload. Uses the raw client so callers keep the HTTP status to explain a failure.
  */
  deleteFile = async (fileId: string, sessionId: string): Promise<{ success: boolean; message: string }> => {
  const response = await this.client.delete(`/upload/${encodeURIComponent(fileId)}`, { params: { session_id: sessionId } });
  return response.data;
  };

  /**
  * sessionuploadfile
  */
  listFiles = async (sessionId: string): Promise<FileListResponse> => {
  return this.get('/upload/list', { session_id: sessionId });
  };
}

export const uploadApi = new UploadApi();
