import React, { useEffect, useRef } from 'react';
import { Spin } from 'antd';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { PlanTaskNode } from '@/types';
import { useT } from '@/i18n/useT';
import { useCanvasPalette, withAlpha, type CanvasPalette } from '@/theme/canvasPalette';

export interface PlanDagVisualizationProps {
  tasks: PlanTaskNode[];
  loading?: boolean;
  height?: number | string;
  onSelectTask?: (task: PlanTaskNode | null) => void;
}

/** vis-network takes literal colour strings, so the node colours come from the resolved theme palette. */
function statusFill(p: CanvasPalette, status: string): { fill: string; ink: string } {
  switch (status) {
  case 'completed': return { fill: p.successBg, ink: p.success };
  case 'running': return { fill: p.actionBg, ink: p.action };
  case 'failed': return { fill: p.errorBg, ink: p.error };
  case 'blocked': return { fill: p.blockedBg, ink: p.blocked };
  case 'pending': return { fill: p.warningBg, ink: p.warning };
  default: return { fill: p.sunken, ink: p.textSecondary };
  }
}

function typeBorder(p: CanvasPalette, type: string): string {
  switch (type) {
  case 'root': return p.blocked;
  case 'composite': return p.action;
  default: return p.borderStrong;
  }
}

const PlanDagVisualization: React.FC<PlanDagVisualizationProps> = ({
  tasks,
  loading,
  height = '480px',
  onSelectTask,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<Network | null>(null);
  const palette = useCanvasPalette();
  const t = useT();

  useEffect(() => {
  if (!containerRef.current || !tasks) {
  return;
  }

  const nodes = new DataSet(
  tasks.map((task) => {
  const label = (task.short_name || task.name || '').slice(0, 40);
  const status = task.status || 'pending';
  const type = task.task_type || 'atomic';
  return {
  id: task.id,
  label: label.length === (task.short_name || task.name || '').length ? label : `${label}…`,
  level: task.depth ?? 0,
  color: {
  background: statusFill(palette, status).fill,
  border: typeBorder(palette, type),
  highlight: {
  background: palette.actionBg,
  border: palette.action,
  },
  },
  shape:
  type === 'root' ? 'star' : type === 'composite' ? 'box' : 'ellipse',
  font: {
  color: statusFill(palette, status).ink,
  face: palette.fontSans,
  size: type === 'root' ? 16 : 14,
  },
  borderWidth: type === 'root' ? 3 : 2,
  shadow: {
  enabled: true,
  color: palette.shadow,
  size: 8,
  x: 2,
  y: 2,
  },
  title: t('dag.nodeTip', { name: task.name, status: task.status, type: task.task_type }),
  };
  })
  );

  const edges = new DataSet(
  tasks
  .filter((task) => task.parent_id)
  .map((task) => ({
  id: `${task.parent_id}-${task.id}`,
  from: task.parent_id!,
  to: task.id,
  arrows: 'to',
  color: {
  color: palette.borderStrong,
  highlight: palette.textTertiary,
  },
  }))
  );

  if (networkRef.current) {
  networkRef.current.setData({ nodes, edges });
  return;
  }

  const network = new Network(
  containerRef.current,
  { nodes, edges },
  {
  layout: {
  hierarchical: {
  enabled: true,
  levelSeparation: 140,
  nodeSpacing: 220,
  treeSpacing: 120,
  direction: 'UD',
  sortMethod: 'directed',
  },
  },
  edges: {
  smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.4 },
  },
  physics: {
  enabled: false,
  },
  interaction: {
  hover: true,
  dragNodes: false,
  },
  }
  );

  networkRef.current = network;

  network.on('selectNode', (params) => {
  const nodeId = params.nodes?.[0];
  if (!nodeId) {
  onSelectTask?.(null);
  return;
  }
  const task = tasks.find((item) => item.id === nodeId) || null;
  onSelectTask?.(task ?? null);
  });

  network.on('deselectNode', () => {
  onSelectTask?.(null);
  });

  return () => {
  network.destroy();
  networkRef.current = null;
  };
  }, [tasks, onSelectTask, palette, t]);

  return (
  <div style={{ position: 'relative', height }}>
  {loading && (
  <div
  style={{
  position: 'absolute',
  inset: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 2,
  background: withAlpha(palette.card, 0.7),
  }}
  >
  <Spin size="large" tip={t('dag.loadingGraph')} />
  </div>
  )}
  <div ref={containerRef} style={{ height: '100%' }} />
  </div>
  );
};

export default PlanDagVisualization;
