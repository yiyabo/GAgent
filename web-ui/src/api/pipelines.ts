import axios from 'axios';
import { BaseApi } from './client';
import { UPLOAD_TIMEOUT_MS } from './upload';
import { ENV } from '@/config/env';

// ---- Contract v1: /pipelines ------------------------------------------------------

export interface Bilingual {
  en: string;
  zh: string;
}

export interface InputPortSpec {
  name: string;
  types: string[];
  title: Bilingual;
  required: boolean;
  multiple: boolean;
}

export interface OutputPortSpec {
  name: string;
  type: string;
  title: Bilingual;
  optional: boolean;
}

export type ParamKind = 'string' | 'text' | 'integer' | 'number' | 'enum' | 'path' | 'accessions';

export interface ParamSpec {
  name: string;
  kind: ParamKind;
  title: Bilingual;
  default: unknown;
  required: boolean;
  choices?: string[];
  minimum?: number;
  maximum?: number;
  max_length?: number;
  help?: Bilingual;
}

export type NodeKind = 'input' | 'ncbi' | 'tool' | 'agent';

export interface NodeTypeSpec {
  type: string;
  category: string;
  kind: NodeKind;
  title: Bilingual;
  description: Bilingual;
  tool: string | null;
  operation: string | null;
  slow: boolean;
  terminal: boolean;
  inputs: InputPortSpec[];
  outputs: OutputPortSpec[];
  params: ParamSpec[];
}

export interface PipelineTemplate {
  id: string;
  title: Bilingual;
  description: Bilingual;
  node_count: number;
}

export interface NodeCatalog {
  spec_version: number;
  any_type: string;
  data_types: Array<{ id: string; title: Bilingual }>;
  categories: Array<{ id: string; title: Bilingual }>;
  node_types: NodeTypeSpec[];
  templates: PipelineTemplate[];
}

export interface PortRef {
  node: string;
  port: string;
}

export interface GraphNode {
  type: string;
  label?: string;
  params?: Record<string, unknown>;
  inputs?: Record<string, PortRef | PortRef[]>;
}

export interface PipelineGraph {
  schema: 1;
  nodes: Record<string, GraphNode>;
  layout?: Record<string, { x: number; y: number }>;
}

export interface ValidationIssue {
  code: string;
  message: string;
  node?: string;
  port?: string;
  param?: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  order: string[];
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export type NodeRunStatus = 'pending' | 'running' | 'succeeded' | 'cached' | 'failed' | 'skipped' | 'cancelled';

export interface PipelineSummary {
  id: string;
  name: string;
  description: string | null;
  latest_version: number;
  created_at: string;
  updated_at: string;
  last_run_status: RunStatus | null;
}

export interface PipelineDetail extends PipelineSummary {
  owner_id: string;
  draft: PipelineGraph;
  versions: Array<{ version: number; graph_hash: string; created_at: string }>;
  active_runs: number;
  validation: ValidationResult;
}

export interface NodeRunOutput {
  name: string;
  type?: string;
  kind?: string;
  size?: number;
}

export interface NodeRun {
  status: NodeRunStatus;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  outputs: Record<string, NodeRunOutput>;
  detail: Record<string, unknown>;
}

export interface PipelineRun {
  id: string;
  pipeline_id: string;
  version: number;
  status: RunStatus;
  inputs: Record<string, string>;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  graph?: PipelineGraph;
  nodes?: Record<string, NodeRun>;
}

export interface PipelineFile {
  name: string;
  path: string;
  size: number;
  modified?: number;
}

export interface OutputPreview {
  kind: 'text' | 'dir' | 'prefix' | 'binary';
  name?: string;
  size?: number;
  text?: string;
  truncated?: boolean;
  entries?: Array<{ path: string; size?: number }>;
}

/** Error carrying the backend message, the HTTP status, and validation details when there are any. */
export class PipelinesApiError extends Error {
  status?: number;
  validation?: ValidationResult;

  constructor(message: string, status?: number, validation?: ValidationResult) {
    super(message);
    this.name = 'PipelinesApiError';
    this.status = status;
    this.validation = validation;
  }
}

function errorFrom(data: unknown): { message: string | null; validation?: ValidationResult } {
  if (!data || typeof data !== 'object') return { message: null };
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === 'string') return { message: detail };
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const item = detail as { message?: unknown; validation?: ValidationResult; problems?: Array<{ message?: string }> };
    const problems = (item.problems || []).map((problem) => problem.message).filter(Boolean).join('; ');
    const message = [typeof item.message === 'string' ? item.message : '', problems].filter(Boolean).join(': ');
    return { message: message || null, validation: item.validation };
  }
  return { message: null };
}

const enc = encodeURIComponent;

export class PipelinesApi extends BaseApi {
  protected async request<T>(
    method: 'get' | 'post' | 'put' | 'patch' | 'delete',
    url: string,
    data?: any,
    params?: Record<string, any>,
    options?: { signal?: AbortSignal },
  ): Promise<T> {
    try {
      const response = await this.client.request({ method, url, data, params, signal: options?.signal });
      return response.data as T;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        const { message, validation } = errorFrom(error.response?.data);
        throw new PipelinesApiError(message || error.message || 'API request failed', error.response?.status, validation);
      }
      throw error;
    }
  }

  status(): Promise<{ enabled: boolean }> {
    return this.get('/pipelines/status');
  }

  catalog(): Promise<NodeCatalog> {
    return this.get('/pipelines/node-types');
  }

  // The collection is /pipelines/items: the gateway serves the bare /pipelines path to this page.
  list(): Promise<{ pipelines: PipelineSummary[] }> {
    return this.get('/pipelines/items');
  }

  create(name: string, start: { description?: string; graph?: PipelineGraph; template?: string } = {}): Promise<PipelineDetail> {
    return this.post('/pipelines/items', { name, ...start });
  }

  detail(id: string): Promise<PipelineDetail> {
    return this.get(`/pipelines/${enc(id)}`);
  }

  save(id: string, changes: { name?: string; description?: string; graph?: PipelineGraph }): Promise<PipelineDetail> {
    return this.put(`/pipelines/${enc(id)}`, changes);
  }

  remove(id: string): Promise<{ deleted: string }> {
    return this.delete(`/pipelines/${enc(id)}`);
  }

  /** Freeze the saved draft as a version; an unchanged draft gives back the latest version. */
  freeze(id: string): Promise<{ pipeline_id: string; version: number; validation: ValidationResult }> {
    return this.post(`/pipelines/${enc(id)}/versions`);
  }

  validate(graph: PipelineGraph, forRun = false): Promise<ValidationResult> {
    return this.request('post', '/pipelines/validate', { graph }, { for_run: forRun });
  }

  files(id: string): Promise<{ files: PipelineFile[] }> {
    return this.get(`/pipelines/${enc(id)}/files`);
  }

  async upload(id: string, file: File, onProgress?: (percent: number) => void): Promise<PipelineFile> {
    const form = new FormData();
    form.append('file', file);
    try {
      // The explicit multipart type matters: with the client's JSON default, axios would turn the form into JSON.
      const response = await this.client.post<PipelineFile>(`/pipelines/${enc(id)}/files`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: UPLOAD_TIMEOUT_MS,
        onUploadProgress: (event) => {
          if (onProgress && event.total) onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
        },
      });
      return response.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        const { message } = errorFrom(error.response?.data);
        throw new PipelinesApiError(message || error.message || 'upload failed', error.response?.status);
      }
      throw error;
    }
  }

  runs(id: string): Promise<{ runs: PipelineRun[] }> {
    return this.get(`/pipelines/${enc(id)}/runs`);
  }

  startRun(id: string, inputs?: Record<string, string>): Promise<PipelineRun> {
    return this.post(`/pipelines/${enc(id)}/runs`, inputs ? { inputs } : {});
  }

  run(runId: string): Promise<PipelineRun> {
    return this.get(`/pipelines/runs/${enc(runId)}`);
  }

  cancelRun(runId: string): Promise<PipelineRun> {
    return this.post(`/pipelines/runs/${enc(runId)}/cancel`);
  }

  preview(runId: string, nodeId: string, port: string, path?: string): Promise<OutputPreview> {
    return this.request('get', `/pipelines/runs/${enc(runId)}/nodes/${enc(nodeId)}/outputs/${enc(port)}/preview`, undefined, path ? { path } : undefined);
  }

  downloadUrl(runId: string, nodeId: string, port: string, path?: string): string {
    const query = path ? `?path=${enc(path)}` : '';
    return `${ENV.API_BASE_URL}/pipelines/runs/${enc(runId)}/nodes/${enc(nodeId)}/outputs/${enc(port)}/download${query}`;
  }
}

export const pipelinesApi = new PipelinesApi();
