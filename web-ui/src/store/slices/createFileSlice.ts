import { ChatSliceCreator } from './types';
import { uploadApi } from '@api/upload';
import { UploadedFile } from '@/types';

export const createFileSlice: ChatSliceCreator = (set, get) => ({
  uploadedFiles: [],
  uploadingFiles: [],

  uploadFile: async (file: File) => {
  const session = get().currentSession;
  if (!session) {
  throw new Error('Please create or select a session first');
  }

  try {
  const response = await uploadApi.uploadFile(file, session.id);
  const uploadedFile: UploadedFile = {
  file_id: response.file_path.split('/').pop()?.split('_')[0] || '',
  file_path: response.file_path,
  file_name: response.file_name,
  original_name: response.original_name,
  file_size: response.file_size,
  file_type: response.file_type,
  uploaded_at: response.uploaded_at,
  category: response.category,
  is_archive: response.is_archive,
  extracted_path: response.extracted_path,
  extracted_files: response.extracted_files,
  };

  set((state) => ({
  uploadedFiles: [...state.uploadedFiles, uploadedFile],
  }));

  return uploadedFile;
  } catch (error) {
  console.error('uploadfilefailed:', error);
  throw error;
  }
  },

  removeUploadedFile: async (fileId: string) => {
  const session = get().currentSession;
  if (!session) {
  return;
  }

  try {
  await uploadApi.deleteFile(fileId, session.id);
  set((state) => ({
  uploadedFiles: state.uploadedFiles.filter((f) => f.file_id !== fileId),
  }));
  } catch (error) {
  console.error('deletefilefailed:', error);
  throw error;
  }
  },

  clearUploadedFiles: () => {
  set({ uploadedFiles: [] });
  },
});
