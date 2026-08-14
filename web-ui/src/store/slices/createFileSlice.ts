import { ChatSliceCreator } from './types';
import { uploadApi } from '@api/upload';
import { UploadedFile } from '@/types';

function isLocalAttachmentRef(file?: UploadedFile | null, fileId?: string): boolean {
  if (!file && !fileId) return true;
  const id = String(fileId || file?.file_id || '');
  if (!id) return true;
  if (id.startsWith('project_')) return true;
  if (file?.category === 'project') return true;
  if (file?.file_type === 'project_reference') return true;
  if (file?.file_size === 'Project File') return true;
  return false;
}

function resolveServerFileId(response: {
  file_id?: string;
  file_path?: string;
  file_name?: string;
}): string {
  if (response.file_id && String(response.file_id).trim()) {
    return String(response.file_id).trim();
  }
  const pathLeaf = (response.file_path || '').split('/').pop() || response.file_name || '';
  if (pathLeaf.includes('_')) {
    return pathLeaf.split('_')[0];
  }
  return pathLeaf;
}

function mapServerFileToUploaded(f: {
  file_id?: string;
  file_path: string;
  file_name: string;
  original_name: string;
  file_size: string;
  uploaded_at: string;
  category?: string;
  is_archive?: boolean;
  extracted_path?: string;
  extracted_files?: number;
}): UploadedFile {
  const pathLeaf = (f.file_path || '').split('/').pop() || f.file_name || '';
  const fileId = resolveServerFileId(f) || pathLeaf;
  const lower = (f.original_name || f.file_name || pathLeaf).toLowerCase();
  let file_type = 'application/octet-stream';
  if (/\.(png|jpe?g|gif|webp|bmp|tiff?)$/.test(lower)) file_type = 'image/*';
  else if (/\.pdf$/.test(lower)) file_type = 'application/pdf';
  else if (/\.(xlsx?|csv|tsv)$/.test(lower)) file_type = 'application/vnd.ms-excel';
  return {
    file_id: fileId,
    file_path: f.file_path,
    file_name: f.file_name || pathLeaf,
    original_name: f.original_name || f.file_name || pathLeaf,
    file_size: f.file_size,
    file_type,
    uploaded_at: f.uploaded_at,
    category: f.category,
    is_archive: f.is_archive,
    extracted_path: f.extracted_path,
    extracted_files: f.extracted_files,
    source: 'server',
  };
}

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
      const serverFileId = resolveServerFileId(response);
      const pathLeaf = response.file_path.split('/').pop() || response.file_name || '';
      const uploadedFile: UploadedFile = {
        file_id: serverFileId || pathLeaf || `${Date.now()}`,
        file_path: response.file_path,
        file_name: response.file_name || pathLeaf,
        original_name: response.original_name,
        file_size: response.file_size,
        file_type: response.file_type,
        uploaded_at: response.uploaded_at,
        category: response.category,
        is_archive: response.is_archive,
        extracted_path: response.extracted_path,
        extracted_files: response.extracted_files,
        source: 'server',
      };

      // Replace mode: purge other session uploads on disk so only the latest remains.
      try {
        await uploadApi.clearUploads(session.id, uploadedFile.file_id);
      } catch (e) {
        console.warn('Failed to clear previous session uploads:', e);
        const previous = get().uploadedFiles.filter((f) => !isLocalAttachmentRef(f));
        await Promise.all(
          previous.map((f) =>
            f.file_id
              ? uploadApi.deleteFile(f.file_id, session.id).catch((err) => {
                  console.warn('Failed to delete previous upload', f.file_id, err);
                })
              : Promise.resolve(),
          ),
        );
      }

      // Re-sync from server truth (disk) so UI never drifts.
      try {
        await get().syncUploadedFilesFromServer();
      } catch {
        const localRefs = get().uploadedFiles.filter((f) => isLocalAttachmentRef(f));
        set({ uploadedFiles: [...localRefs, uploadedFile] });
      }

      return uploadedFile;
    } catch (error) {
      console.error('uploadfilefailed:', error);
      throw error;
    }
  },

  setUploadedFiles: (files: UploadedFile[]) => {
    set({ uploadedFiles: files });
  },

  syncUploadedFilesFromServer: async () => {
    const session = get().currentSession;
    if (!session) {
      set({ uploadedFiles: [] });
      return;
    }
    const sessionId = session.session_id ?? session.id;
    if (!sessionId) {
      set({ uploadedFiles: [] });
      return;
    }

    // Keep project/local refs that are not disk uploads.
    const localRefs = get().uploadedFiles.filter((f) => isLocalAttachmentRef(f));

    try {
      const res = await uploadApi.listFiles(sessionId);
      const serverFiles = (res.files || []).map(mapServerFileToUploaded);
      // Prefer server list as source of truth for real uploads.
      // A local/project ref that duplicates a server file by display name is
      // the same attachment added twice via two entries — drop it so the
      // composer does not render identical chips.
      const serverNames = new Set(
        serverFiles.map((f) => f.original_name || f.file_name),
      );
      const dedupedLocalRefs = localRefs.filter(
        (f) => !serverNames.has(f.original_name || f.file_name),
      );
      set({ uploadedFiles: [...dedupedLocalRefs, ...serverFiles] });
    } catch (error) {
      console.warn('Failed to sync uploads from server:', error);
      // On failure keep localRefs + any existing server-tagged chips.
      const existingServer = get().uploadedFiles.filter((f) => !isLocalAttachmentRef(f));
      set({ uploadedFiles: [...localRefs, ...existingServer] });
    }
  },

  removeUploadedFile: async (fileId: string) => {
    const session = get().currentSession;
    const target = get().uploadedFiles.find((f) => f.file_id === fileId);

    const dropLocal = () => {
      set((state) => ({
        uploadedFiles: state.uploadedFiles.filter((f) => f.file_id !== fileId),
      }));
    };

    if (isLocalAttachmentRef(target, fileId) || !session) {
      dropLocal();
      return;
    }

    await uploadApi.deleteFile(fileId, session.session_id ?? session.id);
    // Confirm against disk
    try {
      await get().syncUploadedFilesFromServer();
    } catch {
      dropLocal();
    }
  },

  clearUploadedFiles: () => {
    set({ uploadedFiles: [] });
  },
});
