/**
 * DAG 3D 可视化工具函数
 * Claude 风格：简洁、优雅、温暖色调
 */

import * as THREE from 'three';
import type { Task as TaskType } from '@/types';

// ============ Claude 风格配色 ============
// 简洁、温暖、高对比度

// 任务类型对应的颜色
export const TASK_TYPE_COLORS = {
  ROOT: {
    primary: '#d97706',      // 琥珀色 - Claude 特征色
    glow: '#f59e0b',
    border: '#b45309',
    text: '#1a1a1a',
    bg: 'rgba(217, 119, 6, 0.08)',
  },
  COMPOSITE: {
    primary: '#2563eb',      // 蓝色 - 清晰、专业
    glow: '#3b82f6',
    border: '#1d4ed8',
    text: '#1a1a1a',
    bg: 'rgba(37, 99, 235, 0.08)',
  },
  ATOMIC: {
    primary: '#16a34a',      // 绿色 - 自然、完成感
    glow: '#22c55e',
    border: '#15803d',
    text: '#1a1a1a',
    bg: 'rgba(22, 163, 74, 0.08)',
  },
  DEFAULT: {
    primary: '#71717a',      // 中性灰
    glow: '#a1a1aa',
    border: '#52525b',
    text: '#1a1a1a',
    bg: 'rgba(113, 113, 122, 0.08)',
  },
};

// 任务状态对应的颜色
export const STATUS_COLORS = {
  completed: '#16a34a',
  done: '#16a34a',
  running: '#2563eb',
  executing: '#2563eb',
  pending: '#d97706',
  failed: '#dc2626',
  error: '#dc2626',
  default: '#71717a',
};

// ============ 布局计算 ============

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
 * 计算 3D 分层布局
 * ROOT 在顶部中心，子节点按层级向下分布
 */
export function calculateTreeLayout(tasks: TaskType[]): {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
} {
  if (!tasks.length) return { nodes: [], edges: [] };

  const nodes: LayoutNode[] = [];
  const edges: LayoutEdge[] = [];

  // 构建父子关系映射
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

  // 找到根节点
  const rootTasks = tasks.filter(t => t.parent_id == null);
  
  // BFS 计算层级
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

  // 布局参数 - 更紧凑的间距
  const levelHeight = 100;
  const nodeSpacing = 50;
  const minRadius = 60;

  const sortedDepths = Array.from(levelMap.keys()).sort((a, b) => a - b);

  sortedDepths.forEach(depth => {
    const tasksInLevel = levelMap.get(depth)!;
    const count = tasksInLevel.length;
    
    // 动态计算半径
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

// ============ Three.js 工厂函数 ============

/**
 * 获取任务类型对应的颜色配置
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
 * 获取状态颜色
 */
export function getStatusColor(status?: string): string {
  if (!status) return STATUS_COLORS.default;
  return STATUS_COLORS[status as keyof typeof STATUS_COLORS] || STATUS_COLORS.default;
}

/**
 * 获取节点尺寸 - 晕染风格适配
 */
export function getNodeSize(taskType?: string): number {
  const type = taskType?.toUpperCase();
  switch (type) {
    case 'ROOT':
      return 16;      // 根节点最大
    case 'COMPOSITE':
      return 12;      // 复合任务中等
    case 'ATOMIC':
      return 9;       // 原子任务较小
    default:
      return 7;
  }
}

/**
 * 创建简洁材质
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
 * 创建边框材质
 */
export function createBorderMaterial(color: string): THREE.LineBasicMaterial {
  return new THREE.LineBasicMaterial({
    color: new THREE.Color(color),
    linewidth: 1,
  });
}

/**
 * 创建圆角矩形形状
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

// ============ 辅助函数 ============

/**
 * 截断文本
 */
export function truncateText(text: string, maxLength: number = 25): string {
  if (!text) return '';
  const cleanText = text.replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
  if (cleanText.length <= maxLength) return cleanText;
  return cleanText.substring(0, maxLength - 3) + '...';
}

/**
 * 格式化任务名称
 */
export function formatTaskName(task: TaskType): string {
  return truncateText(task.name, 30);
}

/**
 * 获取任务类型名称
 */
export function getTaskTypeName(taskType?: string): string {
  switch (taskType?.toUpperCase()) {
    case 'ROOT':
      return '根';
    case 'COMPOSITE':
      return '复合';
    case 'ATOMIC':
      return '原子';
    default:
      return '未知';
  }
}

/**
 * 获取状态名称
 */
export function getStatusName(status?: string): string {
  switch (status) {
    case 'completed':
    case 'done':
      return '已完成';
    case 'running':
    case 'executing':
      return '运行中';
    case 'pending':
      return '待处理';
    case 'failed':
    case 'error':
      return '失败';
    default:
      return '未知';
  }
}
