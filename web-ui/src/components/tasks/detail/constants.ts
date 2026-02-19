export const statusColorMap: Record<string, string> = {
  pending: 'gold',
  running: 'processing',
  completed: 'green',
  failed: 'red',
  skipped: 'default',
};

export const statusLabelMap: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
};
