import type { Task } from './task';

// DAG可视化相关类型
export interface DAGNode {
  id: string;
  label: string;
  group: 'root' | 'composite' | 'atomic';
  status: Task['status'];
  level: number;
  x?: number;
  y?: number;
}

export interface DAGEdge {
  from: string;
  to: string;
  label?: string;
  color?: string;
  dashes?: boolean;
}
