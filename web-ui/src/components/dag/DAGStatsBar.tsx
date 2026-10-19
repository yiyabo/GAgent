import React from 'react';
import { useT } from '@/i18n/useT';

interface DAGStatsBarProps {
  stats: {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  };
}

export const DAGStatsBar: React.FC<DAGStatsBarProps> = ({ stats }) => {
  const t = useT();
  return (
  <div className="dag-stats-bar">
  <div className="dag-stats-item">
  <span className="dag-stats-count">{stats.total}</span>
  <span>{t('dag.total')}</span>
  </div>
  {stats.pending > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot pending" />
  <span>{t('dag.statPending', { count: stats.pending })}</span>
  </div>
  )}
  {stats.running > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot running" />
  <span>{t('dag.statRunning', { count: stats.running })}</span>
  </div>
  )}
  {stats.completed > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot completed" />
  <span>{t('dag.statCompleted', { count: stats.completed })}</span>
  </div>
  )}
  {stats.failed > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot failed" />
  <span>{t('dag.statFailed', { count: stats.failed })}</span>
  </div>
  )}
  </div>
  );
};
