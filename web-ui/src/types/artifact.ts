export interface ArtifactItem {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  modified_at?: string | null;
  extension?: string | null;
}

export interface ArtifactListResponse {
  session_id: string;
  root_path: string;
  items: ArtifactItem[];
  count: number;
}

export interface ArtifactTextResponse {
  path: string;
  content: string;
  truncated: boolean;
}

export interface ArtifactRenderResponse {
  path: string;
  format: 'pdf' | 'html' | 'text';
  url?: string;
  content?: string;
  rendered_at: string;
}

export type DeliverableModule =
  | 'code'
  | 'docs'
  | 'image_tabular'
  | 'paper'
  | 'refs'
  | string;

export interface DeliverableItem {
  module: DeliverableModule;
  path: string;
  name: string;
  status: 'draft' | 'final' | 'superseded' | string;
  size?: number;
  extension?: string | null;
  updated_at?: string | null;
  source_path?: string | null;
}

export interface DeliverableVersionSummary {
  version_id: string;
  created_at?: string | null;
  published_files_count: number;
  published_modules: string[];
}

export interface DeliverableListResponse {
  session_id: string;
  scope: 'latest' | 'history';
  version_id?: string | null;
  root_path: string;
  modules: Record<string, DeliverableItem[]>;
  items: DeliverableItem[];
  count: number;
  paper_status?: Record<string, any>;
  available_versions: DeliverableVersionSummary[];
}

export interface DeliverableManifestResponse {
  session_id: string;
  scope: 'latest' | 'history';
  version_id?: string | null;
  manifest_path?: string | null;
  manifest: Record<string, any>;
  available_versions: DeliverableVersionSummary[];
}
