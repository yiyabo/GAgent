import { BaseApi } from './client';

export interface UploadResponse {
  success: boolean;
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
   * 上传文件
   */
  uploadFile = async (
    file: File,
    sessionId: string
  ): Promise<UploadResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', sessionId);

    // 使用axios直接调用，因为需要设置特殊的Content-Type
    const response = await this.client.post<UploadResponse>('/upload/file', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  };

  /**
   * 删除文件
   */
  deleteFile = async (fileId: string, sessionId: string): Promise<{ success: boolean; message: string }> => {
    return this.delete(`/upload/${fileId}`, { session_id: sessionId });
  };

  /**
   * 列出会话的所有上传文件
   */
  listFiles = async (sessionId: string): Promise<FileListResponse> => {
    return this.get('/upload/list', { session_id: sessionId });
  };
}

export const uploadApi = new UploadApi();
