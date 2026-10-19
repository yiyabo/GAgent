import type { Memory } from '@/types';
import type { TFn } from './useT';

/** The same words appear in the Memory page filters, its table, the detail drawer and the save form. */
export const MEMORY_TYPES: Memory['memory_type'][] = ['conversation', 'experience', 'knowledge', 'context'];
export const MEMORY_IMPORTANCES: Memory['importance'][] = ['critical', 'high', 'medium', 'low', 'temporary'];

export const memoryTypeLabel = (t: TFn, type: Memory['memory_type']): string =>
  t(`memory.type.${type}` as 'memory.type.conversation');

export const memoryImportanceLabel = (t: TFn, importance: Memory['importance']): string =>
  t(`memory.importance.${importance}` as 'memory.importance.critical');

export const memoryTypeOptions = (t: TFn) => MEMORY_TYPES.map((value) => ({ value, label: memoryTypeLabel(t, value) }));
export const memoryImportanceOptions = (t: TFn) => MEMORY_IMPORTANCES.map((value) => ({ value, label: memoryImportanceLabel(t, value) }));
