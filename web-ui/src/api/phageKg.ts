import { BaseApi } from './client';

export interface PhageKgDocument {
  document_id: string;
  file_path?: string;
  shard_id?: string;
  title?: string;
  content?: string;
  content_truncated?: boolean;
  graph_version?: string;
}

class PhageKgApi extends BaseApi {
  getDocument(documentId: string): Promise<PhageKgDocument> {
    return this.get<PhageKgDocument>(`/phage-kg/documents/${encodeURIComponent(documentId)}`);
  }
}

export const phageKgApi = new PhageKgApi();
