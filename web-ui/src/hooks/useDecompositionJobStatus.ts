import { useQuery } from '@tanstack/react-query';
import { planTreeApi } from '@api/planTree';

const FINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled']);

export interface DecompositionJobStatus {
  job_id: string;
  status: string;
  progress?: number;
  total_tasks?: number;
  completed_tasks?: number;
  [key: string]: any;
}

/**
 * Shared hook for polling decomposition job status.
 * Uses React Query to automatically deduplicate requests for the same jobId.
 * Multiple components can subscribe to the same jobId without duplicate API calls.
 */
export function useDecompositionJobStatus(jobId: string | null) {
  return useQuery<DecompositionJobStatus>({
    queryKey: ['decompositionJob', jobId],
    queryFn: () => planTreeApi.getJobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (data) => {
      const status = data?.status;
      if (status && FINAL_JOB_STATUSES.has(status)) {
        return false;
      }
      return 5000;
    },
    staleTime: 4000, // Consider data fresh for 4 seconds
  });
}
