import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { pipelinesApi } from '@api/pipelines';
import type { NodeCatalog, PipelineDetail, PipelineFile, PipelineRun, PipelineSummary } from '@api/pipelines';

const ACTIVE = new Set(['queued', 'running']);

let statusPromise: Promise<boolean> | null = null;

/**
 * Whether this server has node pipelines switched on: null until known. Asked once per page load and
 * shared by every caller; a failed probe counts as off and is asked again next time.
 */
export function usePipelinesStatus(): boolean | null {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    statusPromise ??= pipelinesApi
      .status()
      .then((result) => Boolean(result?.enabled))
      .catch(() => {
        statusPromise = null;
        return false;
      });
    void statusPromise.then((value) => {
      if (alive) setEnabled(value);
    });
    return () => {
      alive = false;
    };
  }, []);
  return enabled;
}

export const usePipelinesEnabled = (): boolean => usePipelinesStatus() === true;

export const useNodeCatalog = () =>
  useQuery<NodeCatalog>({
    queryKey: ['pipelines', 'catalog'],
    queryFn: () => pipelinesApi.catalog(),
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });

export const usePipelineList = () =>
  useQuery<PipelineSummary[]>({
    queryKey: ['pipelines', 'list'],
    queryFn: async () => (await pipelinesApi.list()).pipelines,
    staleTime: 15_000,
    refetchOnWindowFocus: false,
    retry: false,
  });

export const usePipeline = (id: string | null) =>
  useQuery<PipelineDetail>({
    queryKey: ['pipelines', 'detail', id],
    queryFn: () => pipelinesApi.detail(id as string),
    enabled: Boolean(id),
    refetchOnWindowFocus: false,
    retry: false,
  });

export const usePipelineFiles = (id: string | null) =>
  useQuery<PipelineFile[]>({
    queryKey: ['pipelines', 'files', id],
    queryFn: async () => (await pipelinesApi.files(id as string)).files,
    enabled: Boolean(id),
    refetchOnWindowFocus: false,
    retry: false,
  });

export const usePipelineRuns = (id: string | null) =>
  useQuery<PipelineRun[]>({
    queryKey: ['pipelines', 'runs', id],
    queryFn: async () => (await pipelinesApi.runs(id as string)).runs,
    enabled: Boolean(id),
    refetchOnWindowFocus: false,
    retry: false,
    refetchInterval: (data) => (data?.some((run) => ACTIVE.has(run.status)) ? 4000 : false),
  });

/** One run, polled every few seconds while it is queued or running. */
export const usePipelineRun = (runId: string | null) =>
  useQuery<PipelineRun>({
    queryKey: ['pipelines', 'run', runId],
    queryFn: () => pipelinesApi.run(runId as string),
    enabled: Boolean(runId),
    refetchOnWindowFocus: false,
    retry: false,
    refetchInterval: (data) => (data && ACTIVE.has(data.status) ? 3000 : false),
  });

export const isActiveRun = (status?: string | null) => Boolean(status && ACTIVE.has(status));
