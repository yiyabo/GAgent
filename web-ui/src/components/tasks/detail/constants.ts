export const statusColorMap: Record<string, string> = {
  pending: 'gold',
  running: 'processing',
  completed: 'green',
  failed: 'red',
  skipped: 'default',
  blocked: 'orange',
  needs_review: 'gold',
};

export const statusLabelMap: Record<string, string> = {
  pending: 'pending',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  skipped: 'skipped',
  blocked: 'blocked',
  needs_review: 'needs_review',
};
