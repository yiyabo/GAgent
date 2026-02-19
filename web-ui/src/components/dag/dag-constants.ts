import type { Task as TaskType } from '@/types';

export interface GraphNode {
  id: number;
  name: string;
  task: TaskType;
  x?: number;
  y?: number;
  fx?: number;  //  x 
  fy?: number;  //  y 
  rotation: number;  // 
  noteColor: string; // 
}

export interface GraphLink {
  source: number;
  target: number;
  type: 'parent' | 'dependency';  //  or 
}

export const CARD_WIDTH = 130;
export const CARD_HEIGHT = 48;
export const CARD_RADIUS = 3;

export const CARD_COLORS = {
  ROOT: {
  bg: '#fffef8',  // 
  line: '#c9a87c',  // 
  accent: '#b8860b',  // 
  },
  COMPOSITE: {
  bg: '#fdfcf9',  // 
  line: '#8fa3b1',  // 
  accent: '#5c7a8a',  // 
  },
  ATOMIC: {
  bg: '#fffffe',  // 
  line: '#a8b5a0',  // 
  accent: '#6b7c63',  // 
  },
  DEFAULT: {
  bg: '#fafaf8',
  line: '#c0c0c0',
  accent: '#888888',
  },
};

export const STATUS_COLORS_MAP: Record<string, { color: string; bg: string }> = {
  completed: { color: '#2d6a4f', bg: '#d8f3dc' },
  done: { color: '#2d6a4f', bg: '#d8f3dc' },
  running: { color: '#1d4e89', bg: '#cfe2f3' },
  executing: { color: '#1d4e89', bg: '#cfe2f3' },
  pending: { color: '#9a6b00', bg: '#fff3cd' },
  failed: { color: '#9c2230', bg: '#f8d7da' },
  error: { color: '#9c2230', bg: '#f8d7da' },
  default: { color: '#666666', bg: '#f0f0f0' },
};

export function getCardColors(taskType?: string) {
  const type = taskType?.toUpperCase();
  switch (type) {
  case 'ROOT': return CARD_COLORS.ROOT;
  case 'COMPOSITE': return CARD_COLORS.COMPOSITE;
  case 'ATOMIC': return CARD_COLORS.ATOMIC;
  default: return CARD_COLORS.DEFAULT;
  }
}
