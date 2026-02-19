import type { Task as TaskType } from '@/types';

export interface GraphNode {
  id: number;
  name: string;
  task: TaskType;
  x?: number;
  y?: number;
  fx?: number;  // 固定 x 位置
  fy?: number;  // 固定 y 位置
  rotation: number;  // 便签倾斜角度
  noteColor: string; // 便签颜色
}

export interface GraphLink {
  source: number;
  target: number;
  type: 'parent' | 'dependency';  // 父子关系 or 依赖关系
}

// 紧凑卡片尺寸
export const CARD_WIDTH = 130;
export const CARD_HEIGHT = 48;
export const CARD_RADIUS = 3;

// 索引卡片配色 - 温暖的纸质色系
export const CARD_COLORS = {
  ROOT: {
    bg: '#fffef8',        // 象牙白
    line: '#c9a87c',      // 金棕色线
    accent: '#b8860b',    // 暗金色
  },
  COMPOSITE: {
    bg: '#fdfcf9',        // 奶油白
    line: '#8fa3b1',      // 灰蓝色线
    accent: '#5c7a8a',    // 石板蓝
  },
  ATOMIC: {
    bg: '#fffffe',        // 纯净白
    line: '#a8b5a0',      // 灰绿色线
    accent: '#6b7c63',    // 橄榄绿
  },
  DEFAULT: {
    bg: '#fafaf8',
    line: '#c0c0c0',
    accent: '#888888',
  },
};

// 状态对应的颜色 - 更柔和
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

// 获取卡片颜色配置
export function getCardColors(taskType?: string) {
  const type = taskType?.toUpperCase();
  switch (type) {
    case 'ROOT': return CARD_COLORS.ROOT;
    case 'COMPOSITE': return CARD_COLORS.COMPOSITE;
    case 'ATOMIC': return CARD_COLORS.ATOMIC;
    default: return CARD_COLORS.DEFAULT;
  }
}
