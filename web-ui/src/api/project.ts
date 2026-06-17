import { BaseApi } from './client';

export interface DataRoot {
  path: string;
  label?: string;
  mode: string;
}

export interface ModelProvider {
  base_url: string;
  api_key: string;
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
  getProject = async (projectId: number): Promise<ProjectResponse> => {
    return this.get<ProjectResponse>(`/project/${projectId}`);
  };

  getProjectFiles = async (
    projectId: number,
    path?: string,
    dataRootIndex: number = 0
  ): Promise<FileTreeResponse> => {
    const params: Record<string, any> = { data_root_index: dataRootIndex };
    if (path) {
      params.path = path;
    }
    return this.get<FileTreeResponse>(`/project/${projectId}/files`, params);
  };

  selectFiles = async (
    projectId: number,
    selectedPaths: string[],
    sessionId?: string
  ): Promise<SelectedFilesResponse> => {
    return this.post<SelectedFilesResponse>(`/project/${projectId}/select-files`, {
      project_id: projectId,
      selected_paths: selectedPaths,
      session_id: sessionId,
    });
  };
}

export const projectApi = new ProjectApi();
