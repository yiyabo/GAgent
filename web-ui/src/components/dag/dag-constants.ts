import type { Task as TaskType } from '@/types';
import type { CanvasPalette } from '@/theme/canvasPalette';

export interface GraphNode {
  id: number;
  name: string;
  task: TaskType;
  x?: number;
  y?: number;
  fx?: number;  //  x 
  fy?: number;  //  y 
  rotation: number;  // 
}

export interface GraphLink {
  source: number;
  target: number;
  type: 'parent' | 'dependency';  //  or 
}

export const CARD_WIDTH = 130;
export const CARD_HEIGHT = 48;
export const CARD_RADIUS = 3;

/** Everything a card needs, resolved from the active theme. The graph is drawn on a canvas that
 *  cannot read CSS variables, so callers pass in the palette read by `useCanvasPalette`. */
export interface CardColors {
  /** Card face. */
  bg: string;
  /** Hairline around the card. */
  line: string;
  /** The left bar and the selection ring: marks the task type. */
  accent: string;
}

export interface StatusColors {
  /** The status dot. */
  color: string;
  /** A tint of the same hue, for chips and legends. */
  bg: string;
}

export function getCardColors(p: CanvasPalette, taskType?: string): CardColors {
  switch (taskType?.toUpperCase()) {
  case 'ROOT': return { bg: p.card, line: p.borderStrong, accent: p.action };
  case 'COMPOSITE': return { bg: p.card, line: p.borderDefault, accent: p.textSecondary };
  case 'ATOMIC': return { bg: p.card, line: p.borderSubtle, accent: p.textTertiary };
  default: return { bg: p.sunken, line: p.borderSubtle, accent: p.textTertiary };
  }
}

export function getStatusColors(p: CanvasPalette, status?: string): StatusColors {
  switch (status) {
  case 'completed':
  case 'done': return { color: p.success, bg: p.successBg };
  case 'running':
  case 'executing': return { color: p.action, bg: p.actionBg };
  case 'pending': return { color: p.warning, bg: p.warningBg };
  case 'blocked': return { color: p.blocked, bg: p.blockedBg };
  case 'failed':
  case 'error': return { color: p.error, bg: p.errorBg };
  default: return { color: p.skipped, bg: p.sunken };
  }
}
