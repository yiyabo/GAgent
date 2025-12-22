import { BaseApi } from './client';
import { ENV } from '@/config/env';
import type { ArtifactListResponse, ArtifactTextResponse } from '@/types';

class ArtifactsApi extends BaseApi {
  listSessionArtifacts = async (
    sessionId: string,
    options?: { maxDepth?: number; includeDirs?: boolean; limit?: number; extensions?: string }
  ): Promise<ArtifactListResponse> => {
    return this.get<ArtifactListResponse>(`/artifacts/sessions/${sessionId}`, {
      max_depth: options?.maxDepth,
      include_dirs: options?.includeDirs,
      limit: options?.limit,
      extensions: options?.extensions,
    });
  };

  getSessionArtifactText = async (
    sessionId: string,
    path: string,
    options?: { maxBytes?: number }
  ): Promise<ArtifactTextResponse> => {
    return this.get<ArtifactTextResponse>(`/artifacts/sessions/${sessionId}/text`, {
      path,
      max_bytes: options?.maxBytes,
    });
  };
}

export const artifactsApi = new ArtifactsApi();

export const buildArtifactFileUrl = (sessionId: string, path: string) => {
  const encoded = encodeURIComponent(path);
  return `${ENV.API_BASE_URL}/artifacts/sessions/${sessionId}/file?path=${encoded}`;
};
