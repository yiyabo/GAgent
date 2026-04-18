/**
 * DAG 3D 
 * Claude : , , 
 */

import * as THREE from 'three';
import type { Task as TaskType } from '@/types';


export const TASK_TYPE_COLORS = {
  ROOT: {
  primary: '#d97706',  //  - Claude 
  glow: '#f59e0b',
  border: '#b45309',
  text: '#1a1a1a',
  bg: 'rgba(217, 119, 6, 0.08)',
  },
  COMPOSITE: {
  primary: '#2563eb',  //  - , 
  glow: '#3b82f6',
  border: '#1d4ed8',
  text: '#1a1a1a',
  bg: 'rgba(37, 99, 235, 0.08)',
  },
  ATOMIC: {
  primary: '#16a34a',  //  - , completed
  glow: '#22c55e',
  border: '#15803d',
  text: '#1a1a1a',
  bg: 'rgba(22, 163, 74, 0.08)',
  },
  DEFAULT: {
  primary: '#71717a',  // medium
  glow: '#a1a1aa',
  border: '#52525b',
  text: '#1a1a1a',
  bg: 'rgba(113, 113, 122, 0.08)',
  },
};

export const STATUS_COLORS = {
  completed: '#16a34a',
  done: '#16a34a',
  running: '#2563eb',
  executing: '#2563eb',
  pending: '#d97706',
  failed: '#dc2626',
  error: '#dc2626',
  blocked: '#ea580c',
  default: '#71717a',
};


export interface LayoutNode {
  id: number;
  x: number;
  y: number;
  z: number;
  task: TaskType;
}

export interface LayoutEdge {
  source: number;
  target: number;
}

/**
 *  3D 
 * ROOT medium, 
 */
export function calculateTreeLayout(tasks: TaskType[]): {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
} {
  if (!tasks.length) return { nodes: [], edges: [] };

  const nodes: LayoutNode[] = [];
  const edges: LayoutEdge[] = [];

  const taskMap = new Map<number, TaskType>();
  const childrenMap = new Map<number, TaskType[]>();
  
  tasks.forEach(task => {
  taskMap.set(task.id, task);
  
  if (task.parent_id != null) {
  const parentId = Number(task.parent_id);
  const taskId = Number(task.id);
  edges.push({ source: parentId, target: taskId });
  
  if (!childrenMap.has(parentId)) {
  childrenMap.set(parentId, []);
  }
  childrenMap.get(parentId)!.push(task);
  }
  });

  const rootTasks = tasks.filter(t => t.parent_id == null);
  
  const levelMap = new Map<number, TaskType[]>();
  const visited = new Set<number>();
  
  interface QueueItem {
  task: TaskType;
  depth: number;
  }
  
  const queue: QueueItem[] = rootTasks.map(task => ({ task, depth: 0 }));
  
  while (queue.length > 0) {
  const { task, depth } = queue.shift()!;
  
  if (visited.has(task.id)) continue;
  visited.add(task.id);
  
  if (!levelMap.has(depth)) {
  levelMap.set(depth, []);
  }
  levelMap.get(depth)!.push(task);
  
  const children = childrenMap.get(task.id) || [];
  children.forEach(child => {
  if (!visited.has(child.id)) {
  queue.push({ task: child, depth: depth + 1 });
  }
  });
  }

  const levelHeight = 100;
  const nodeSpacing = 50;
  const minRadius = 60;

  const sortedDepths = Array.from(levelMap.keys()).sort((a, b) => a - b);

  sortedDepths.forEach(depth => {
  const tasksInLevel = levelMap.get(depth)!;
  const count = tasksInLevel.length;
  
  const circumference = count * nodeSpacing;
  const calculatedRadius = circumference / (2 * Math.PI);
  const radius = Math.max(minRadius, calculatedRadius);
  
  const y = -depth * levelHeight;

  tasksInLevel.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));

  tasksInLevel.forEach((task, index) => {
  let x = 0, z = 0;

  if (depth === 0 || count === 1) {
  x = 0;
  z = 0;
  } else {
  const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
  x = Math.cos(angle) * radius;
  z = Math.sin(angle) * radius;
  }

  nodes.push({ id: task.id, x, y, z, task });
  });
  });

  return { nodes, edges };
}


/**
 * Get color configuration for task type.
 */
export function getTaskTypeColors(taskType?: string) {
  const type = taskType?.toUpperCase();
  switch (type) {
  case 'ROOT':
  return TASK_TYPE_COLORS.ROOT;
  case 'COMPOSITE':
  return TASK_TYPE_COLORS.COMPOSITE;
  case 'ATOMIC':
  return TASK_TYPE_COLORS.ATOMIC;
  default:
  return TASK_TYPE_COLORS.DEFAULT;
  }
}

/**
 * Get color by task status.
 */
export function getStatusColor(status?: string): string {
  if (!status) return STATUS_COLORS.default;
  return STATUS_COLORS[status as keyof typeof STATUS_COLORS] || STATUS_COLORS.default;
}

/**
 * Get 3D node size by task type.
 */
export function getNodeSize(taskType?: string): number {
  const type = taskType?.toUpperCase();
  switch (type) {
  case 'ROOT':
  return 16;  // ROOT
  case 'COMPOSITE':
  return 12;  // COMPOSITE
  case 'ATOMIC':
  return 9;  // ATOMIC
  default:
  return 7;
  }
}

/**
 * Create simple mesh material.
 */
export function createSimpleMaterial(color: string, opacity: number = 1): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
  color: new THREE.Color(color),
  transparent: opacity < 1,
  opacity,
  roughness: 0.4,
  metalness: 0.1,
  });
}

/**
 * Create border line material.
 */
export function createBorderMaterial(color: string): THREE.LineBasicMaterial {
  return new THREE.LineBasicMaterial({
  color: new THREE.Color(color),
  linewidth: 1,
  });
}

/**
 * Create rounded rectangle shape.
 */
export function createRoundedRectShape(
  width: number,
  height: number,
  radius: number
): THREE.Shape {
  const shape = new THREE.Shape();
  const x = -width / 2;
  const y = -height / 2;

  shape.moveTo(x + radius, y);
  shape.lineTo(x + width - radius, y);
  shape.quadraticCurveTo(x + width, y, x + width, y + radius);
  shape.lineTo(x + width, y + height - radius);
  shape.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  shape.lineTo(x + radius, y + height);
  shape.quadraticCurveTo(x, y + height, x, y + height - radius);
  shape.lineTo(x, y + radius);
  shape.quadraticCurveTo(x, y, x + radius, y);

  return shape;
}


/**
 * Truncate task label text.
 */
export function truncateText(text: string, maxLength: number = 25): string {
  if (!text) return '';
  const cleanText = text.replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
  if (cleanText.length <= maxLength) return cleanText;
  return cleanText.substring(0, maxLength - 3) + '...';
}

/**
 * Format display name for task labels.
 */
export function formatTaskName(task: TaskType): string {
  return truncateText(task.name, 30);
}

/**
 * Get readable task type label.
 */
export function getTaskTypeName(taskType?: string): string {
  switch (taskType?.toUpperCase()) {
  case 'ROOT':
  return 'ROOT';
  case 'COMPOSITE':
  return 'COMPOSITE';
  case 'ATOMIC':
  return 'ATOMIC';
  default:
  return 'UNKNOWN';
  }
}

/**
 * getstatusname
 */
export function getStatusName(status?: string): string {
  switch (status) {
  case 'completed':
  case 'done':
  return 'completed';
  case 'running':
  case 'executing':
  return 'medium';
  case 'pending':
  return '';
  case 'failed':
  case 'error':
  return 'failed';
  case 'blocked':
  return 'blocked';
  default:
  return '';
  }
}
