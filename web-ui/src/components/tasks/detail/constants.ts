export const statusColorMap: Record<string, string> = {
  pending: 'gold',
  running: 'processing',
  completed: 'green',
  failed: 'red',
  skipped: 'default',
};

export const statusLabelMap: Record<string, string> = {
  pending: 'execute',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  skipped: '',
};
