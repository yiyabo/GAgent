import { BaseApi } from './client';

export const UPLOAD_TIMEOUT_MS = 60 * 60 * 1000;

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
  sessionId: string
  ): Promise<UploadResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('session_id', sessionId);

  const response = await this.client.post<UploadResponse>('/upload/file', formData, {
  headers: {
  'Content-Type': 'multipart/form-data',
  },
  // Sequencing data can take minutes to send; the 30 s API default would abort it.
  timeout: UPLOAD_TIMEOUT_MS,
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
