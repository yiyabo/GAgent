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
  completed: '已执行',
  failed: 'failed',
  skipped: 'skipped',
  blocked: 'blocked',
  needs_review: '待验收',
};
