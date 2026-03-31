import { BaseApi } from './client';
import { ENV } from '@/config/env';
import type {
  ArtifactListResponse,
  ArtifactTextResponse,
  ArtifactRenderResponse,
  DeliverableListResponse,
  DeliverableManifestResponse,
} from '@/types';

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

  listSessionDeliverables = async (
    sessionId: string,
    options?: {
      scope?: 'latest' | 'history';
      version?: string;
      includeDraft?: boolean;
      module?: string;
      limit?: number;
    }
  ): Promise<DeliverableListResponse> => {
    return this.get<DeliverableListResponse>(`/artifacts/sessions/${sessionId}/deliverables`, {
      scope: options?.scope ?? 'latest',
      version: options?.version,
      include_draft: options?.includeDraft,
      module: options?.module,
      limit: options?.limit,
    });
  };

  getSessionDeliverablesManifest = async (
    sessionId: string,
    options?: { scope?: 'latest' | 'history'; version?: string }
  ): Promise<DeliverableManifestResponse> => {
    return this.get<DeliverableManifestResponse>(
      `/artifacts/sessions/${sessionId}/deliverables/manifest`,
      {
        scope: options?.scope ?? 'latest',
        version: options?.version,
      }
    );
  };

  getSessionDeliverableText = async (
    sessionId: string,
    path: string,
    options?: { version?: string; maxBytes?: number }
  ): Promise<ArtifactTextResponse> => {
    return this.get<ArtifactTextResponse>(`/artifacts/sessions/${sessionId}/deliverables/text`, {
      path,
      version: options?.version,
      max_bytes: options?.maxBytes,
    });
  };

  /**
   * Render file to preview format (LaTeX -> PDF, Markdown -> HTML)
   */
  renderArtifact = async (
    sessionId: string,
    path: string,
    options?: { sourceType?: 'raw' | 'deliverables'; version?: string }
  ): Promise<ArtifactRenderResponse> => {
    const params: Record<string, string> = { path };
    if (options?.sourceType) params.source_type = options.sourceType;
    if (options?.version) params.version = options.version;
    return this.get<ArtifactRenderResponse>(`/artifacts/sessions/${sessionId}/render`, params);
  };
}

export const artifactsApi = new ArtifactsApi();

export const buildArtifactFileUrl = (sessionId: string, path: string) => {
  const encoded = encodeURIComponent(path);
  return `${ENV.API_BASE_URL}/artifacts/sessions/${sessionId}/file?path=${encoded}`;
};

export const buildWorkspaceFileUrl = (sessionId: string, path: string) => {
  const encoded = encodeURIComponent(path);
  return `${ENV.API_BASE_URL}/artifacts/sessions/${sessionId}/workspace-file?path=${encoded}`;
};

export const buildDeliverableFileUrl = (
  sessionId: string,
  path: string,
  options?: { version?: string }
) => {
  const encodedPath = encodeURIComponent(path);
  const encodedVersion = options?.version ? `&version=${encodeURIComponent(options.version)}` : '';
  return `${ENV.API_BASE_URL}/artifacts/sessions/${sessionId}/deliverables/file?path=${encodedPath}${encodedVersion}`;
};

/**
 * Build full URL for rendered file (PDF from LaTeX, etc.)
 */
export const buildRenderedFileUrl = (renderPath: string) => {
  // renderPath is like "/artifacts/rendered/{filename}" or just "{filename}"
  const cleanPath = renderPath.startsWith('/') ? renderPath : `/artifacts/rendered/${renderPath}`;
  return `${ENV.API_BASE_URL}${cleanPath}`;
};
