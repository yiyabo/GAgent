import type { Task } from './task';

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
