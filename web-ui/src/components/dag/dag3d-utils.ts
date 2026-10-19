/**
 * Small helpers for the task graph view: labels and the tag colour names antd understands.
 * (The WebGL/three.js code that used to live here was never called and is gone.)
 */

import type { Task as TaskType } from '@/types';

/** antd preset tag colours; index.css maps each preset onto the theme's status tokens. */
export type TagPreset = 'blue' | 'geekblue' | 'green' | 'gold' | 'red' | 'purple' | 'default';

/** Tag colour for a task type: root stands out, composite and atomic stay quiet. */
export function getTaskTypeTag(taskType?: string): TagPreset {
  switch (taskType?.toUpperCase()) {
  case 'ROOT': return 'blue';
  case 'COMPOSITE': return 'geekblue';
  case 'ATOMIC': return 'default';
  default: return 'default';
  }
}

/** Tag colour for a task status. */
export function getStatusTag(status?: string): TagPreset {
  switch (status) {
  case 'completed':
  case 'done': return 'green';
  case 'running':
  case 'executing': return 'blue';
  case 'pending': return 'gold';
  case 'blocked': return 'purple';
  case 'failed':
  case 'error': return 'red';
  default: return 'default';
  }
}

/** Marker size for a task type; also used for the memory graph's node scale. */
export function getNodeSize(taskType?: string): number {
  switch (taskType?.toUpperCase()) {
  case 'ROOT': return 16;
  case 'COMPOSITE': return 12;
  case 'ATOMIC': return 9;
  default: return 7;
  }
}

/** Truncate a task label, dropping the ROOT:/COMPOSITE:/ATOMIC: prefix. */
export function truncateText(text: string, maxLength: number = 25): string {
  if (!text) return '';
  const cleanText = text.replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
  if (cleanText.length <= maxLength) return cleanText;
  return cleanText.substring(0, maxLength - 3) + '...';
}

export function formatTaskName(task: TaskType): string {
  return truncateText(task.name, 30);
}

export function getTaskTypeName(taskType?: string): string {
  switch (taskType?.toUpperCase()) {
  case 'ROOT': return 'ROOT';
  case 'COMPOSITE': return 'COMPOSITE';
  case 'ATOMIC': return 'ATOMIC';
  default: return 'UNKNOWN';
  }
}

export function getStatusName(status?: string): string {
  switch (status) {
  case 'completed':
  case 'done': return 'completed';
  case 'running':
  case 'executing': return 'running';
  case 'pending': return 'pending';
  case 'failed':
  case 'error': return 'failed';
  case 'blocked': return 'blocked';
  default: return '';
  }
}
