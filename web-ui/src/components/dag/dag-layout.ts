import type { Task as TaskType } from '@/types';

// 紧凑布局参数
export const NODE_WIDTH = 140;
export const HORIZONTAL_GAP = 12;
export const VERTICAL_GAP = 56;

// 计算紧凑树形布局
export function calculateHierarchicalLayout(tasks: TaskType[]): Map<number, { x: number; y: number }> {
  const positions = new Map<number, { x: number; y: number }>();
  if (!tasks.length) return positions;

  // 构建父子关系
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

  // 对每个父节点的子节点按 position 排序
  childrenMap.forEach(children => {
    children.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));
  });

  // 找到根节点
  const rootTasks = tasks.filter(t => t.parent_id == null);
  rootTasks.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));

  // 缓存子树宽度
  const subtreeWidthCache = new Map<number, number>();

  // 递归计算子树宽度
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

  // 递归布局子树
  function layoutSubtree(taskId: number, centerX: number, y: number) {
    positions.set(taskId, { x: centerX, y });
    
    const children = childrenMap.get(taskId) || [];
    if (children.length === 0) return;

    // 计算子节点的总宽度
    const childWidths = children.map(c => getSubtreeWidth(c.id));
    const totalChildrenWidth = childWidths.reduce((sum, w) => sum + w, 0) 
      + (children.length - 1) * HORIZONTAL_GAP;
    
    // 子节点起始位置（使子节点组居中于父节点下方）
    let childX = centerX - totalChildrenWidth / 2;
    const childY = y + VERTICAL_GAP;

    children.forEach((child, i) => {
      const childWidth = childWidths[i];
      const childCenterX = childX + childWidth / 2;
      layoutSubtree(child.id, childCenterX, childY);
      childX += childWidth + HORIZONTAL_GAP;
    });
  }

  // 布局所有根节点
  if (rootTasks.length === 1) {
    layoutSubtree(rootTasks[0].id, 0, 0);
  } else {
    // 多个根节点的情况
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
