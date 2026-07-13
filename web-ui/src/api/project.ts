import { BaseApi } from './client';

export interface DataRoot {
  path: string;
  label?: string;
  mode: string;
}

export interface ModelProvider {
  type?: string;
  model?: string;
  base_url: string;
  model_options?: string[];
}

export interface ProjectData {
  id: number;
  data_roots: DataRoot[];
  model_provider?: ModelProvider;
}

export interface ProjectResponse {
  code: number;
  message: string;
  data?: ProjectData;
}

export interface FileTreeNode {
  key: string;
  title: string;
  path: string;
  is_leaf: boolean;
  children?: FileTreeNode[];
}

export interface FileTreeResponse {
  code: number;
  message: string;
  data: FileTreeNode[];
}

export interface FileReference {
  path: string;
  name: string;
  data_root_path: string;
}

export interface SelectedFilesResponse {
  code: number;
  message: string;
  files: FileReference[];
}

export class ProjectApi extends BaseApi {
  getProject = async (projectId: number): Promise<ProjectResponse> =>
    this.get<ProjectResponse>(`/project/${projectId}`);

  getProjectFiles = async (projectId: number): Promise<FileTreeResponse> =>
    this.get<FileTreeResponse>(`/project/${projectId}/files`);

  selectFiles = async (
    projectId: number,
    selectedPaths: string[],
    dataRootIndex: number,
    sessionId?: string,
  ): Promise<SelectedFilesResponse> =>
    this.post<SelectedFilesResponse>(`/project/${projectId}/select-files`, {
      project_id: projectId,
      selected_paths: selectedPaths,
      data_root_index: dataRootIndex,
      session_id: sessionId,
    });
}

export const projectApi = new ProjectApi();
