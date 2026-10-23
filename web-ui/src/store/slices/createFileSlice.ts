import axios from 'axios';
import { ChatSliceCreator, ChatState } from './types';
import { uploadApi } from '@api/upload';
import { translate } from '@/i18n/useT';
import { ChatSession, UploadedFile } from '@/types';

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

function apiSessionId(session: ChatSession): string {
  return session.session_id ?? session.id;
}

/**
 * The composer holds the attachments of the message the user has not sent yet, one list per chat
 * (`attachmentDrafts`, keyed by the local session id). `uploadedFiles` mirrors the current chat's list.
 * Sending a message takes the list, so a file belongs to exactly one message once it is sent and can no
 * longer be removed from the composer while the agent works on it.
 */
export const createFileSlice: ChatSliceCreator = (set, get) => {
  const writeDraft = (key: string, update: (files: UploadedFile[]) => UploadedFile[]) => {
    if (!key) return;
    set((state: ChatState) => {
      const next = update(state.attachmentDrafts?.[key] ?? []);
      const attachmentDrafts = { ...(state.attachmentDrafts ?? {}), [key]: next };
      return state.currentSession?.id === key ? { attachmentDrafts, uploadedFiles: next } : { attachmentDrafts };
    });
  };

  return ({
  uploadedFiles: [],
  attachmentDrafts: {},
  uploadingFiles: [],

  uploadFile: async (file: File) => {
    const session = get().currentSession;
    if (!session) {
      throw new Error(translate('upload.needSession'));
    }
    // An upload that finishes after the user switched chats still belongs to the chat it started in.
    const key = session.id;
    const uploadId = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    set((state: ChatState) => ({
      uploadingFiles: [...(state.uploadingFiles ?? []), { id: uploadId, name: file.name, sessionKey: key }],
    }));

    try {
      const response = await uploadApi.uploadFile(file, apiSessionId(session));
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
      writeDraft(key, (files) => [...files.filter((f) => f.file_id !== uploadedFile.file_id), uploadedFile]);
      return uploadedFile;
    } finally {
      set((state: ChatState) => ({
        uploadingFiles: (state.uploadingFiles ?? []).filter((u) => u.id !== uploadId),
      }));
    }
  },

  setUploadedFiles: (files: UploadedFile[]) => {
    const session = get().currentSession;
    if (!session) {
      set({ uploadedFiles: files });
      return;
    }
    writeDraft(session.id, () => files);
  },

  syncUploadedFilesFromServer: async () => {
    const session = get().currentSession;
    if (!session) {
      set({ uploadedFiles: [] });
      return;
    }
    const key = session.id;
    const drafts = get().attachmentDrafts?.[key] ?? [];
    set({ uploadedFiles: drafts });

    // Drop chips whose file was deleted on the server meanwhile, so a message never names a missing file.
    // Only files present when the request started are checked; a file uploaded during the request stays.
    const checked = drafts.filter((f) => !isLocalAttachmentRef(f)).map((f) => f.file_id);
    if (checked.length === 0) return;
    try {
      const res = await uploadApi.listFiles(apiSessionId(session));
      const present = new Set((res.files || []).map((f) => resolveServerFileId(f)));
      const gone = new Set(checked.filter((id) => !present.has(id)));
      if (gone.size === 0) return;
      writeDraft(key, (files) => files.filter((f) => !gone.has(f.file_id)));
    } catch (error) {
      console.warn('Failed to check pending attachments against the server:', error);
    }
  },

  removeUploadedFile: async (fileId: string) => {
    const session = get().currentSession;
    if (!session) return;
    const key = session.id;
    const target = (get().attachmentDrafts?.[key] ?? []).find((f) => f.file_id === fileId);
    if (!target) return;

    if (!isLocalAttachmentRef(target, fileId)) {
      try {
        await uploadApi.deleteFile(fileId, apiSessionId(session));
      } catch (error) {
        // Already gone on the server: the chip is all that is left to remove.
        if (!(axios.isAxiosError(error) && error.response?.status === 404)) throw error;
      }
    }
    writeDraft(key, (files) => files.filter((f) => f.file_id !== fileId));
  },

  takeComposerAttachments: () => {
    const session = get().currentSession;
    if (!session) return [];
    const files = get().attachmentDrafts?.[session.id] ?? [];
    if (files.length > 0) writeDraft(session.id, () => []);
    return files;
  },

  clearUploadedFiles: () => {
    const session = get().currentSession;
    if (!session) {
      set({ uploadedFiles: [] });
      return;
    }
    writeDraft(session.id, () => []);
  },
  });
};
