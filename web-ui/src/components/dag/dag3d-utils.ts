/**
 * Small helpers for the task graph view: labels and the tag colour names antd understands.
 * (The WebGL/three.js code that used to live here was never called and is gone.)
 */

import type { Task as TaskType } from '@/types';
import type { TFn } from '@/i18n/useT';

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

/** Display name of a task type, in the current language. */
export function getTaskTypeName(t: TFn, taskType?: string): string {
  switch (taskType?.toUpperCase()) {
  case 'ROOT': return t('dag.type.root');
  case 'COMPOSITE': return t('dag.type.composite');
  case 'ATOMIC': return t('dag.type.atomic');
  default: return t('dag.type.unknown');
  }
}

/** Display name of a task status, in the current language. */
export function getStatusName(t: TFn, status?: string): string {
  switch (status) {
  case 'completed':
  case 'done': return t('dag.status.completed');
  case 'running':
  case 'executing': return t('dag.status.running');
  case 'pending': return t('dag.status.pending');
  case 'failed':
  case 'error': return t('dag.status.failed');
  case 'blocked': return t('dag.status.blocked');
  default: return t('dag.status.unknown');
  }
}
