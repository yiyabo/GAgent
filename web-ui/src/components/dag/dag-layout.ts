import type { Task as TaskType } from '@/types';

export const NODE_WIDTH = 140;
export const HORIZONTAL_GAP = 12;
export const VERTICAL_GAP = 56;

export function calculateHierarchicalLayout(tasks: TaskType[]): Map<number, { x: number; y: number }> {
  const positions = new Map<number, { x: number; y: number }>();
  if (!tasks.length) return positions;

  const childrenMap = new Map<number, TaskType[]>();
  const taskMap = new Map<number, TaskType>();
  
  tasks.forEach(task => {
    taskMap.set(task.id, task);
    if (task.parent_id != null) {
      if (!childrenMap.has(task.parent_id)) {
        childrenMap.set(task.parent_id, []);
      }
      childrenMap.get(task.parent_id)!.push(task);
    }
  });

  childrenMap.forEach(children => {
    children.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));
  });

  const rootTasks = tasks.filter(t => t.parent_id == null);
  rootTasks.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));

  const subtreeWidthCache = new Map<number, number>();

  function getSubtreeWidth(taskId: number): number {
    if (subtreeWidthCache.has(taskId)) {
      return subtreeWidthCache.get(taskId)!;
    }
    
    const children = childrenMap.get(taskId) || [];
    let width: number;
    
    if (children.length === 0) {
      width = NODE_WIDTH;
    } else {
      width = 0;
      children.forEach((child, i) => {
        width += getSubtreeWidth(child.id);
        if (i < children.length - 1) {
          width += HORIZONTAL_GAP;
        }
      });
      width = Math.max(NODE_WIDTH, width);
    }
    
    subtreeWidthCache.set(taskId, width);
    return width;
  }

  function layoutSubtree(taskId: number, centerX: number, y: number) {
    positions.set(taskId, { x: centerX, y });
    
    const children = childrenMap.get(taskId) || [];
    if (children.length === 0) return;

    const childWidths = children.map(c => getSubtreeWidth(c.id));
    const totalChildrenWidth = childWidths.reduce((sum, w) => sum + w, 0) 
      + (children.length - 1) * HORIZONTAL_GAP;
    
    let childX = centerX - totalChildrenWidth / 2;
    const childY = y + VERTICAL_GAP;

    children.forEach((child, i) => {
      const childWidth = childWidths[i];
      const childCenterX = childX + childWidth / 2;
      layoutSubtree(child.id, childCenterX, childY);
      childX += childWidth + HORIZONTAL_GAP;
    });
  }

  if (rootTasks.length === 1) {
    layoutSubtree(rootTasks[0].id, 0, 0);
  } else {
    const rootWidths = rootTasks.map(r => getSubtreeWidth(r.id));
    const totalRootWidth = rootWidths.reduce((sum, w) => sum + w, 0) 
      + (rootTasks.length - 1) * HORIZONTAL_GAP;
    let rootX = -totalRootWidth / 2;
    
    rootTasks.forEach((root, i) => {
      const rootWidth = rootWidths[i];
      const rootCenterX = rootX + rootWidth / 2;
      layoutSubtree(root.id, rootCenterX, 0);
      rootX += rootWidth + HORIZONTAL_GAP;
    });
  }

  return positions;
}
